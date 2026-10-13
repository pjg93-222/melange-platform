# -*- coding: utf-8 -*-
"""Regression contracts for the completed 2026-07-16 Moneyball audit.

These tests are intentionally cheap: they exercise pure kernel behavior and source-level
integration contracts without importing the run-on-import production engines.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))


def source(name: str) -> str:
    matches = [
        path for path in FEC.rglob(name)
        if not any(
            part in {"tests", "attic", "__pycache__"} or part.startswith("_quarantine")
            for part in path.relative_to(FEC).parts[:-1]
        )
    ]
    if len(matches) != 1:
        raise AssertionError("producer basename %s resolved to %r" % (name, matches))
    return matches[0].read_text(encoding="utf-8-sig")


def data_root() -> Path:
    """Resolve the live data root the same way in and outside a linked worktree."""
    root = Path(os.environ.get("MONEYBALL_ROOT", FEC.parent))
    if not (root / "category_stance.json").exists():
        root = FEC.parents[3]
    return root


class OverallCrownContracts(unittest.TestCase):
    def test_tier_one_is_sufficient_for_champion(self):
        import moneyball_core as core

        matrix = {"x%d" % i: "Persuadable" for i in range(8)}
        cap = {"Someone": {"raw": "Champion", "final": "Ally"}}
        base = core.overall_tier(matrix)
        self.assertNotEqual("Champion", base)
        self.assertEqual("Champion", core.overall_display_tier(matrix, 1, "Someone", cap))
        self.assertEqual(base, core.overall_display_tier(matrix, 2, "Someone", cap))
        self.assertEqual(base, core.overall_display_tier(matrix, 0, "Someone", cap))

    def test_ally_floor_requires_full_ally_strength(self):
        import moneyball_core as core

        bands = dict((tier, cut) for cut, tier in core.OVERALL_BANDS)
        self.assertEqual(3.00, bands["Ally"])
        self.assertEqual(2.00, bands["Highly Persuadable"])
        self.assertNotIn("Champion", bands)
        self.assertEqual("Ally", core.overall_tier({"a": "Ally", "b": "Ally"}))

        root = data_root()
        matrix = json.loads((root / "category_stance.json").read_text(encoding="utf-8"))["matrix"]
        # RE-PINNED 2026-10-11 (post-refresh chain): Britt's live overall tier is now Ally (>= 3.00);
        # the band contract above is the durable part, this line pins the published value.
        self.assertEqual("Ally", core.overall_tier(matrix["Katie Britt"]))

    def test_invalid_opponent_cutoff_is_rejected(self):
        import moneyball_core as core

        with self.assertRaises(ValueError):
            core.validate_overall_bands(((0.0, "Skeptic"), (-0.5, "Highly Skeptical")), -0.1)

    def test_overall_bands_are_the_strict_fixed_ladder(self):
        import moneyball_core as core

        bands = dict((tier, cut) for cut, tier in core.OVERALL_BANDS)
        self.assertEqual(
            {
                "Ally": 3.00,
                "Highly Persuadable": 2.00,
                "Persuadable": 1.00,
                "Lean Persuadable": 0.40,
                "True Neutral": 0.00,
                "Lean Skeptic": -0.30,
                "Skeptic": -0.75,
                "Highly Skeptical": -1.50,
            },
            bands,
        )
        self.assertEqual("True Neutral", core.overall_tier_from_value(0.0))
        self.assertNotIn(
            core.overall_tier_from_value(0.01),
            {"Lean Skeptic", "Skeptic", "Highly Skeptical", "Opponent"},
        )
        self.assertNotIn(
            core.overall_tier_from_value(-0.01),
            {"Highly Persuadable", "Persuadable", "Lean Persuadable", "Ally"},
        )


class TierMathContracts(unittest.TestCase):
    def test_legacy_policy_pair_remains_84_legislation_16_press(self):
        import tier_util

        legislative, press = tier_util.load_tier_weights()
        # Backward-compatible policy pair. The primacy entry point deliberately
        # computes Leg% * factor and does not call this legacy two-pillar blend.
        self.assertAlmostEqual(0.84, legislative)
        self.assertAlmostEqual(0.16, press)
        self.assertAlmostEqual(1.0, legislative + press)

    def test_tier_score_uses_existing_pillar_scale_without_second_percentile_rank(self):
        import tier_util

        pcts = {
            "French Hill": {"leg": 0.90, "press": 0.80},
            "Daniel Meuser": {"leg": 0.998, "press": 0.414},
            "Young Kim": {"leg": 0.80, "press": 0.90},
        }
        scores = tier_util.legislative_press_scores(pcts, 0.30, 0.25)
        self.assertAlmostEqual(0.47, scores["French Hill"])
        self.assertAlmostEqual(0.4029, scores["Daniel Meuser"])
        self.assertAlmostEqual(0.465, scores["Young Kim"])
        self.assertGreater(scores["French Hill"], scores["Daniel Meuser"])

    def test_primacy_conduct_and_score_based_top13(self):
        import tier_util

        root = data_root()
        roster = json.loads((root / "category_stance.json").read_text(encoding="utf-8"))["matrix"]
        tiers, ranks = tier_util.legislative_press_tiers(list(roster), root=str(root))

        self.assertEqual({}, tier_util.load_tier_overrides())
        self.assertEqual({}, tier_util.load_tier_positions())
        # RE-PINNED 2026-07-29 twice. First the factor-only directive seated
        # Huizenga and Kim in Tier 2. Then the same-day uncertainty penalty
        # (K=0.25, "certainty outranks estimation") re-seated Kim in Tier 1
        # at slot 13 on her full-evidence measurement (lambda=1, no ledger
        # re-size) and left Huizenga at 16 — his gap is structural
        # bank/crypto ballast, which the purity law prices. Vargas Tier 1 is
        # a ratified constraint of the K=0.25 choice ("Vargas needs to be
        # in"). PRIMACY_BOARD=legxfactor restores the original seating,
        # which the revert test below still pins.
        # RE-PINNED 2026-10-11 to the RE-1 G1 board (deviation-form grade, 7683ba952) as
        # republished by the post-refresh chain: Cortez Masto T3 #44, Huizenga + Kim T1, Crapo T2 #21.
        self.assertEqual(1, tiers.get("Brad Sherman"))
        self.assertEqual(5, ranks.get("Brad Sherman"))
        self.assertEqual(3, tiers.get("Catherine Cortez Masto"))
        self.assertEqual(44, ranks.get("Catherine Cortez Masto"))
        self.assertEqual(1, tiers.get("Tim Scott"))
        self.assertLessEqual(ranks.get("Tim Scott"), 12)
        self.assertEqual(1, tiers.get("Bill Huizenga"))
        self.assertEqual(10, ranks.get("Bill Huizenga"))
        # Kim held Tier 1 by 0.002 under the uncertainty penalty, then
        # returned to Tier 2 when amendments took measure weight 0.5 the same
        # day; Crapo took the vacated seat. Adjudication credit unchanged.
        self.assertEqual(1, tiers.get("Young Kim"))
        self.assertEqual(11, ranks.get("Young Kim"))
        self.assertEqual(2, tiers.get("Mike Crapo"))
        self.assertEqual(21, ranks.get("Mike Crapo"))
        self.assertEqual(1, tiers.get("Juan Vargas"))
        self.assertEqual(6, ranks.get("Juan Vargas"))
        self.assertEqual(3, tiers.get("Andy Barr"))
        self.assertNotEqual(1, tiers.get("French Hill"))
        self.assertEqual(13, sum(value == 1 for value in tiers.values()))
        self.assertEqual(24, sum(value == 2 for value in tiers.values()))
        self.assertEqual(48, sum(value == 3 for value in tiers.values()))

    def test_revert_switch_restores_pre_directive_tier1_seating(self):
        import tier_util

        root = data_root()
        roster = json.loads(
            (root / "category_stance.json").read_text(encoding="utf-8")
        )["matrix"]
        factors = tier_util.load_primacy_factors(str(root))
        pcts = json.loads(
            (root / "ranks_python.json").read_text(encoding="utf-8")
        )["pcts"]
        scores = tier_util.primacy_scores(
            pcts, factors, mode="legxfactor"
        )
        order = sorted(
            (m for m in roster if m in scores),
            key=lambda m: (-scores[m], m),
        )
        slot = {m: i + 1 for i, m in enumerate(order)}
        # RE-PINNED 2026-10-11: the 07-29 pre-directive seating (all five inside Tier 1) no longer
        # holds on live inputs; these are the legxfactor slots of the post-refresh publication. The two
        # tier-count lines that followed read an undefined `tiers` and were dropped (the sibling test pins them).
        self.assertEqual({"Bill Huizenga": 9, "Young Kim": 14, "Catherine Cortez Masto": 89,
                          "Brad Sherman": 5, "Tim Scott": 4},
                         {m: slot[m] for m in ("Bill Huizenga", "Young Kim", "Catherine Cortez Masto", "Brad Sherman", "Tim Scott")})


class LegislativeRoutingContracts(unittest.TestCase):
    def test_known_cdfi_and_privacy_bills_have_one_effective_domain(self):
        import bill_rules as rules

        self.assertEqual(
            "CDFI Fund",
            rules.override_for({}, "H.R.7733", 117)["Category"],
        )
        self.assertEqual(
            "CDFI Fund",
            rules.override_for({}, "S.869", 118)["Category"],
        )
        for bill, congress in (("H.R.7036", 118), ("H.R.7022", 117), ("S.4698", 117)):
            self.assertEqual(
                "Data Privacy & Cyber",
                rules.override_for({}, bill, congress)["Category"],
            )

    def test_user_bill_override_wins_over_builtin_and_role_dedup_prefers_highest_role(self):
        import bill_rules as rules

        user = {
            (rules.bill_key("H.R.7733"), "117"): {
                "Bill": "H.R.7733",
                "Congress": "117",
                "Category": "Payments",
                "Include": "1",
            }
        }
        self.assertEqual(
            "Payments",
            rules.override_for(user, "H.R.7733", 117)["Category"],
        )
        self.assertEqual("original-cosponsor", rules.preferred_role(("cosponsor", "original-cosponsor")))
        self.assertEqual("sponsor", rules.preferred_role(("cosponsor", "sponsor", "original-cosponsor")))
        self.assertEqual(2.0, rules.role_weight(rules.preferred_role(("cosponsor", "original-cosponsor"))))


class MediaRoutingContracts(unittest.TestCase):
    def test_press_target_beats_bad_declared_tags(self):
        import stance.media_rules as rules

        decision = rules.press_routing_decision(
            target="Bean Social Security simplification bill",
            title="Bean Bill to Simplify Social Security Moves Forward",
            declared=("Technology & Emerging Issues",),
            body="This release concerns Social Security retirement ages.",
        )
        self.assertEqual([], decision["categories"])
        self.assertEqual("no-policy-domain", decision["basis"])

    def test_press_routes_specific_target_and_keeps_residual_general_explicit(self):
        import stance.media_rules as rules

        late_fee = rules.press_routing_decision(
            target="Barr CRA to nullify the CFPB credit card late fee rule",
            title="House, Senate resolutions would void CFPB credit card late fees rule",
            declared=("General Credit Union",),
        )
        self.assertEqual(
            ["CFPB Issues & Structural Reform", "Credit Card & Consumer-Fee Mandates"],
            late_fee["categories"],
        )
        general = rules.press_routing_decision(
            target="League contribution to NAF for credit union priorities",
            title="League advances credit union priorities",
            declared=("General Credit Union",),
        )
        self.assertEqual(["General Credit Union"], general["categories"])
        self.assertEqual("target", general["basis"])

    def test_social_context_guards_block_road_and_non_cu_tax_false_positives(self):
        import stance.media_rules as rules

        self.assertNotIn(
            "Interchange",
            rules.route_raw_categories("Ribbon cutting opens a new highway interchange and exit ramp"),
        )
        self.assertNotIn(
            "Credit Union Tax Status",
            rules.route_raw_categories("The PGA Foundation is a tax-exempt nonprofit organization"),
        )
        self.assertNotIn(
            "CU-Bank Competitive Dynamics",
            rules.route_raw_categories("Visited a local community bank to discuss agriculture"),
        )

    def test_full_audit_persists_own_source_channel_integrity_guards(self):
        src = (FEC / "audit" / "audit_full.py").read_text(encoding="utf-8-sig")
        for token in ("own_source_channels", "own-source channel sidecars preserve source row identity",
                      "validated own-source press channels are enabled and loaded",
                      "hearing channel remains gold-only", "Bluesky remains single-counted"):
            self.assertIn(token, src)


class BarrOverrideContracts(unittest.TestCase):
    def test_barr_late_fee_efforts_are_explicit_support_gold(self):
        tweet_overrides = json.loads(
            (FEC.parent / "data" / "stance" / "tweet_stance_overrides.json").read_text(encoding="utf-8")
        )["corrections"]
        tweet_gold = {
            row["quorum_id"]: row["stance"]
            for row in tweet_overrides
        }
        for quorum_id in ("511658226", "511657485", "498264314", "458365083"):
            self.assertEqual("support", tweet_gold.get(quorum_id))

        press_overrides = json.loads(
            (FEC.parent / "data" / "stance" / "press_stance_overrides.json").read_text(encoding="utf-8")
        )["corrections"]
        match = next(
            row
            for row in press_overrides
            if "House, Senate resolutions would void CFPB credit card late fees rule"
            in row.get("title_contains", "")
        )
        self.assertEqual("support", match["stance"])

    def test_worktree_gold_overlays_live_data_without_dropping_live_only_rows(self):
        import config_overlay

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live = base / "live"
            code = base / "code"
            live.mkdir()
            code.mkdir()
            (live / "gold.json").write_text(
                json.dumps({"corrections": [
                    {"quorum_id": "1", "stance": "neutral"},
                    {"quorum_id": "2", "stance": "support"},
                ]}),
                encoding="utf-8",
            )
            (code / "gold.json").write_text(
                json.dumps({"corrections": [
                    {"quorum_id": "1", "stance": "support"},
                ]}),
                encoding="utf-8",
            )
            rows = config_overlay.merged_rows(
                "gold.json",
                "corrections",
                ("quorum_id",),
                data_root=str(live),
                code_root=str(code),
            )
        self.assertEqual(
            {"1": "support", "2": "support"},
            {row["quorum_id"]: row["stance"] for row in rows},
        )


class FollowupSocialGoldContracts(unittest.TestCase):
    def test_britt_and_lummis_user_ratified_posts_are_support(self):
        rows = json.loads(
            (FEC.parent / "data" / "stance" / "tweet_stance_overrides.json").read_text(encoding="utf-8")
        )["corrections"]
        gold = {str(row.get("quorum_id")): row.get("stance") for row in rows}
        support_ids = {
            "559107834", "559097784",  # Britt: anti-debanking, Facebook/X cross-post
            "552715581", "552700010",  # Britt: opposition to CFPB credit-reporting overreach
            "545211528",               # Britt: CFPB accountability column
            "433093684",               # Britt: LSCU visit and opposition to harmful IRS action
            "591392565", "591305285",  # Lummis: open banking / anti-big-bank obstruction
            "539036369",               # Lummis: CBDC violates Fourth Amendment
            "536549427", "536545303",  # Lummis: anti-CBDC / pro-digital-assets cross-posts
            "445704177",               # Lummis: explicit opposition to U.S. CBDC
        }
        self.assertEqual(
            support_ids,
            {quorum_id for quorum_id in support_ids if gold.get(quorum_id) == "support"},
        )


class ValenceRegistryContracts(unittest.TestCase):
    def test_cbdc_target_is_anti_cu_so_opposition_composes_to_support(self):
        registry = json.loads(
            (FEC.parent / "data/stance/cu_valence_registry.json").read_text(encoding="utf-8")
        )["entries"]
        cbdc = next(row for row in registry if row.get("key") == "cbdc")
        self.assertEqual(-1, cbdc["valence"])
        tweet_stack = source("tweet_stance_stack.py")
        self.assertIn(
            "CO.merged_rows('data/stance/cu_valence_registry.json','entries',('key',),data_root=B)",
            tweet_stack.replace("\n", ""),
        )

    def test_three_reader_unanimity_beats_meta_but_not_manual_gold(self):
        import stance.stance_rules as stance_rules

        self.assertEqual(
            "support",
            stance_rules.enforce_reader_unanimity(
                "oppose", ("support", "support", "support")
            ),
        )
        self.assertEqual(
            "oppose",
            stance_rules.enforce_reader_unanimity(
                "oppose", ("support", "support", "neutral")
            ),
        )
        stack = source("tweet_stance_stack.py")
        self.assertLess(
            stack.index("enforce_reader_unanimity"),
            stack.index("_ov = tweet_override("),
        )


class TweetImageContracts(unittest.TestCase):
    def test_image_evidence_only_resolves_weak_text_and_conflicts_go_to_review(self):
        import stance.tweet_image_evidence as image_evidence

        strong_text = image_evidence.fuse_stance(
            text_stance="support",
            text_tier="unanimous",
            image_stance="oppose",
            image_confidence=0.99,
        )
        self.assertEqual("support", strong_text["stance"])
        self.assertEqual("conflict-review", strong_text["image_disposition"])

        resolved = image_evidence.fuse_stance(
            text_stance="neutral",
            text_tier="review",
            image_stance="support",
            image_confidence=0.90,
        )
        self.assertEqual("support", resolved["stance"])
        self.assertEqual("image-resolved", resolved["image_disposition"])

        weak = image_evidence.fuse_stance(
            text_stance="unrelated",
            text_tier="review",
            image_stance="support",
            image_confidence=0.60,
        )
        self.assertEqual("unrelated", weak["stance"])
        self.assertEqual("low-confidence-review", weak["image_disposition"])

    def test_x_collector_requests_and_persists_attachment_metadata(self):
        collector = source("collect/tweet_collector.py")
        self.assertIn("attachments", collector)
        self.assertIn("attachments.media_keys", collector)
        self.assertIn("media.fields", collector)
        self.assertIn("media_json", collector)

    def test_canonical_pipeline_refreshes_tweet_labels_before_media_blend(self):
        pipeline = source("run_pipeline.py")
        self.assertLess(
            pipeline.index("('tweet_stance_stack.py',['--score'])"),
            pipeline.index("('build_media_blend.py',[])"),
        )


class AllocationContracts(unittest.TestCase):
    def test_coalition_candidates_exclude_unconditional_ally_seeds(self):
        text = source("per_topic_coalition.py").replace(" ", "")
        self.assertIn("ifcampT[n]=='neu'", text)
        self.assertNotIn("orcs[n]=='Ally'", text)
        self.assertIn("joint=spread(funded)", text)

    def test_topic_flip_blocks_system_level_skeptics_and_never_overspends(self):
        text = source("per_topic_flip.py").replace(" ", "")
        self.assertIn("BLOCK_OVERALL", text)
        self.assertIn("OVT", text)
        self.assertIn("spent+INC<=BUDGET", text)

    def test_frontier_only_removes_contest_opponent_after_activation(self):
        text = source("budget_frontier.py").replace(" ", "")
        self.assertIn("activated=", text)
        self.assertIn("aa=anti-activated", text)
        self.assertIn("if-negeff<=1e-12:break", text)

    def test_scoped_frontier_and_optimizer_runs_cannot_clobber_production(self):
        frontier = source("budget_frontier.py")
        optimizer = source("optimize.py")
        self.assertIn("scoped_output_path", frontier)
        self.assertIn("scoped_output_path", optimizer)
        self.assertIn("'topic':ONLY", frontier.replace(" ", ""))
        self.assertIn("'topic':ONLY", optimizer.replace(" ", ""))
        self.assertIn("'knee_found':knee_found", frontier.replace(" ", ""))

    def test_optimizer_budget_and_certificate_are_honest(self):
        text = source("optimize.py").replace(" ", "")
        self.assertIn("spent+INC<=BUDGET", text)
        self.assertIn("selected_marginals", text)
        self.assertIn("conversion_reachable", text)
        self.assertIn("certificate_valid", text)
        self.assertIn("'mode':optimizer_mode", text)
        self.assertIn("'guarantee':guarantee", text)
        self.assertIn("'observations':observations", text)
        self.assertIn("defconcavity_violations()", text)
        self.assertIn("'kind':'exact_discrete_separable_concave'", text)
        self.assertIn("certified_gain_ratio=1.0ifcertificate_validelseNone", text)
        self.assertIn("opt_bound=curifcertificate_validelseNone", text)
        self.assertNotIn("ADAPTIVEOPTIMIZER", text)


class DashboardContracts(unittest.TestCase):
    def test_canvas_blank_click_and_touch_clear_selection_and_refit(self):
        template = source("surfaces/templates/make_dashboard_html_00.tpl").replace(" ", "")
        self.assertIn("elsebackToTargets()", template)
        self.assertIn("functionbackToTargets(){", template)
        self.assertIn("fit();draw();", template)

    def test_head_to_head_uses_pressure_multiplier_in_math_and_inverse(self):
        template = source("surfaces/templates/make_dashboard_html_00.tpl").replace(" ", "")
        self.assertIn("pm=PM?PM[sel]:1", template)
        self.assertIn("K.WM*pm*", template)
        self.assertIn("/(K.WM*pm)", template)

    def test_max_cascade_is_closed_network_edge_activation_not_a_flip_star(self):
        template = source("surfaces/templates/make_dashboard_html_00.tpl")
        compact = template.replace(" ", "")
        self.assertIn("function pageRankScores(", template)
        self.assertIn("function closedCascadeRoute(", template)
        self.assertIn("closedCascadeRoute(sel,6,2)", compact)
        self.assertIn("outdeg", template)
        self.assertIn("pathProb", template)
        max_route = template.split("function maxCascadeRoute()", 1)[1].split("function flipRoute()", 1)[0]
        self.assertNotIn("directOnly:true", max_route)
        self.assertIn("plan.edges", max_route)
        self.assertIn("u,v", max_route)
        self.assertIn("BLOCKOV", template)

    def test_max_route_discloses_hub_convergence_as_measurement_not_a_named_roster(self):
        """2026-09-19 ruling: keep the Max-route algorithm, DISCLOSE its hub convergence.

        The owner chose disclosure over redesign because the hubs are genuine, so this pins the
        two halves of that ruling: the route math is untouched, and the line that admits the
        convergence is MEASURED from the page's own engine rather than naming three members.
        """
        template = source("surfaces/templates/make_dashboard_html_00.tpl")
        compact = template.replace(" ", "")
        max_route = template.split("function maxCascadeRoute()", 1)[1].split("function flipRoute()", 1)[0]
        # One display dial, and BOTH the line and the sweep behind it hang off it.
        self.assertIn("const SHOW_HUB_CORE=true;", template)
        self.assertIn("if(SHOW_HUB_CORE)hubCoreStart();", compact)
        self.assertIn("SHOW_HUB_CORE?`<divid=hubcore>${hubCoreHTML()}</div>`:''", compact)
        self.assertIn("if(!SHOW_HUB_CORE||!hubCore", compact)
        # Generated from the page's own gate + closedCascadeRoute over every seed. A hard-coded
        # "Hill / Emmer / Barr" would read identically on screen and silently go stale, so the
        # renderer is checked to hold no member name at all.
        self.assertIn("closedCascadeRoute(seeds[at++],6,2).nodes", compact)
        self.assertIn("mesc(DATA.name[i].split(' ').slice(-1)[0])", template)
        render = template.split("function hubCoreHTML()", 1)[1].split("function hubCoreFill(", 1)[0]
        # MemberProfileLinks in test_bug_report_regressions binds every `class=lnk data-i=`
        # renderer to emit a profile address; this line is one, so it carries mprof() too.
        self.assertIn("</span>${mprof(i)}", render)
        for named in ("French Hill", "Tom Emmer", "Andy Barr", "Hill", "Emmer", "Barr"):
            self.assertNotIn(named, render, "the hub-core line must be measured, not a named roster")
        # Top 3 by share, integer percentages, and a denominator of seeds that YIELD a route --
        # maxCascadeRoute's own no-edge refusal -- which is route_replay.json's "viable" count.
        self.assertIn("top:top.slice(0,3)", compact)
        self.assertIn("Math.round(sh*100)", compact)
        self.assertIn("if(nd.length<2)continue;", compact)
        self.assertIn("hits[i]/routes", compact)
        self.assertIn("max-cascade routes", template)
        self.assertIn("most routes converge on these members.", template)
        # Cached on exactly the three inputs closedCascadeRoute reads that can change, re-checked
        # on every slice so a mid-sweep configuration change drops the run instead of mixing two
        # configurations, and re-checked at render so a cached line cannot outlive its key.
        self.assertIn("returnissue+'|'+NETCFG+'|'+[...dropped]", compact)
        self.assertIn("if(hubCoreKeyNow()!==key){hubCoreRun=null;return;}", compact)
        self.assertIn("hubCore.key!==hubCoreKeyNow()", compact)
        # Lazy and sliced: scheduled only by a Max-route request, never at page load, and never
        # holding the main thread for the ~1.0 s the full sweep costs.
        self.assertEqual(template.count("hubCoreStart()"), 2,
                         "the sweep must be reachable only from a max-route request")
        self.assertIn("if(SHOW_HUB_CORE)hubCoreStart();", max_route.replace(" ", ""))
        self.assertIn("setTimeout(step,0)", compact)
        self.assertIn("performance.now()-t0<HUBSLICE", compact)
        # Display-only: no hub-core state inside the engine, and the line sits between the
        # existing fanout hint and the existing step list rather than replacing either.
        cascade_engine = template.split("function closedCascadeRoute(", 1)[1].split("function cascadeReach(", 1)[0]
        self.assertNotIn("hubCore", cascade_engine)
        self.assertLess(max_route.index("no fixed flip target"), max_route.index("<div id=hubcore>"))
        self.assertLess(max_route.index("<div id=hubcore>"), max_route.index("plan.steps.map"))


class CrossEngineContracts(unittest.TestCase):
    def test_full_audit_separates_worktree_code_root_from_live_data_root(self):
        audit = source("audit/audit_full.py")
        self.assertIn("FE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))", audit.replace(" ", ""))
        self.assertIn("DATA_FE=os.path.join(B,'fec')", audit.replace(" ", ""))
        self.assertIn("open(os.path.join(DATA_FE, 'legislators.csv')", audit)
        self.assertNotIn("B=MB_ROOT; FE=B+r'\\fec'", audit)

    def test_full_audit_refuses_a_foreign_live_pipeline_writer(self):
        audit = source("audit/audit_full.py")
        self.assertIn("def assert_no_foreign_pipeline_writer", audit)
        self.assertIn("refusing audit while a foreign Moneyball pipeline owns the artifact set", audit)
        self.assertIn("owner not in (os.getpid(),parent_pid)", audit)

    def test_full_audit_checks_dashboard_and_workbook_crown_parity(self):
        audit = source("audit/audit_full.py")
        self.assertIn("dashboard overall labels exactly match shared crown law", audit)
        self.assertIn("workbook stance column exactly matches shared crown law", audit)
        self.assertIn("_ra_ws=_wb['Rank & Allocation']", audit)
        self.assertIn("_wb_display={_ra_ws.cell", audit)

    def test_salience_parameters_have_one_shared_implementation(self):
        core = source("moneyball_core.py")
        shock = source("salience_shock.py")
        bundle = source("build_dashboard_bundle.py")
        self.assertIn("def salience_issue_params", core)
        self.assertIn("MBC.salience_issue_params", shock)
        self.assertIn("salience_issue_params", bundle)

    def test_dashboard_and_compact_layout_use_kernel_network_authority(self):
        bundle = source("build_dashboard_bundle.py")
        layout = source("sna_layout.py")
        self.assertIn("select_network_file", bundle)
        self.assertIn("load_network", layout)
        self.assertIn("dump_atomic", layout)
        self.assertIn("sorted(", layout)


if __name__ == "__main__":
    unittest.main()
