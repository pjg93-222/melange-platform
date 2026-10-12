from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

import district.district_cu_metrics as dcm
import district.demographic_normalization as dn
import stance.stance_rules as sr


class CurvedStanceLabelTests(unittest.TestCase):
    def test_requested_domains_are_the_only_declared_curves(self) -> None:
        self.assertEqual(
            sr.CURVED_CATEGORIES,
            {
                "Member Business Lending",
                "Mortgage & Housing",
                "Charter Modernization",
                "Technology & Emerging",
            },
        )

    def test_curve_is_tie_aware_sign_preserving_and_leaves_raw_scores(self) -> None:
        category = "Member Business Lending"
        scores = {
            "low": {category: 0.1, "Other": 0.42},
            "tie_a": {category: 0.2, "Other": -0.2},
            "tie_b": {category: 0.2, "Other": 0.0},
            "high": {category: 0.9, "Other": 0.7},
            "negative_low": {category: -0.1, "Other": 0.1},
            "negative_high": {category: -0.8, "Other": 0.2},
            "empty": {category: 0.0, "Other": 0.3},
        }
        signals = {
            member: {category: member != "empty", "Other": True}
            for member in scores
        }
        original = json.loads(json.dumps(scores))
        curved = sr.curve_label_scores(scores, signals, {category})
        self.assertEqual(scores, original)
        self.assertEqual(curved["tie_a"][category], curved["tie_b"][category])
        self.assertLess(curved["low"][category], curved["high"][category])
        self.assertGreater(curved["negative_low"][category], -1)
        self.assertEqual(curved["negative_high"][category], -1)
        self.assertGreaterEqual(curved["low"][category], 0)
        self.assertLessEqual(curved["negative_low"][category], 0)
        self.assertEqual(curved["empty"][category], 0)
        self.assertEqual(curved["low"]["Other"], scores["low"]["Other"])


class CreditUnionMetricTests(unittest.TestCase):
    def test_cu_lq_and_longitudinal_proxy_reconstruct_exactly(self) -> None:
        artifact = {
            "cbp_year": 2023,
            "weights": {"method": "lodes-block-employment"},
            "sources": {"us": {"url": "https://www2.census.gov/cbp.zip"}},
            "districts": {
                "AA-01": {
                    "four_digit": {
                        "1111": {"emp": 40, "est": 4},
                        "5221": {"emp": 60, "est": 6},
                    },
                    "cu_benchmark": {
                        "emp_522130": 10,
                        "est_522130": 1,
                    },
                },
                "AA-02": {
                    "four_digit": {
                        "1111": {"emp": 100, "est": 10},
                        "5221": {"emp": 200, "est": 20},
                    },
                    "cu_benchmark": {
                        "emp_522130": 15,
                        "est_522130": 3,
                    },
                },
            },
        }
        industry = {
            "window": [2013, 2023],
            "lq_national": {"CNS10": 0.8},
            "growth_quotient": {"CNS10": 1.2},
            "shift_share": {
                "CNS10": {
                    "national_growth": 2,
                    "industry_mix": -1,
                    "competitive_shift": 3,
                }
            },
        }
        packed = dcm.CreditUnionMetrics(artifact).for_geo(
            "AA-01",
            industry,
            {"CNS10": {"naics": "52", "name": "Finance and Insurance"}},
        )
        self.assertEqual(packed["snapshot"]["employment_lq"], 1.6)
        self.assertEqual(packed["snapshot"]["establishment_lq"], 1.0)
        self.assertEqual(
            packed["longitudinal_proxy"]["growth_quotient"], 1.2
        )
        self.assertEqual(
            packed["longitudinal_proxy"]["shift_share"]["delta"], 4.0
        )


class SenatePopulationNormalizationTests(unittest.TestCase):
    def test_apportionment_table_is_complete_and_sums_to_house_total(self):
        self.assertEqual(len(dn.HOUSE_SEATS_BY_STATE), 50)
        self.assertEqual(sum(dn.HOUSE_SEATS_BY_STATE.values()), 435)
        self.assertEqual(dn.HOUSE_SEATS_BY_STATE["CA"], 52)
        self.assertEqual(dn.HOUSE_SEATS_BY_STATE["TX"], 38)

    def test_senate_population_is_divided_before_normalization(self):
        basis = dn.senate_population_basis(
            38_703_144, "Senate", "CA"
        )
        self.assertEqual(basis["house_seats"], 52)
        self.assertAlmostEqual(
            basis["normalization_population"],
            38_703_144 / 52,
        )
        self.assertIsNone(
            dn.senate_population_basis(713_251, "House", "CA")
        )

    def test_population_percentiles_are_tie_aware(self):
        ranks = dn.midrank_percentiles([
            (0, 500_000),
            (1, 750_000),
            (2, 750_000),
            (3, 1_000_000),
        ])
        self.assertEqual(ranks[0], 0.0)
        self.assertEqual(ranks[1], ranks[2])
        self.assertEqual(ranks[1], 0.5)
        self.assertEqual(ranks[3], 1.0)


class DashboardRenderingContractTests(unittest.TestCase):
    def test_ratified_district_briefs_cover_and_render_for_every_member(self) -> None:
        artifact = json.loads(
            (ROOT / "data" / "district" / "district_briefs_v1.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (ROOT / "data" / "district" / "district_brief_evidence_v1.json").read_text(
                encoding="utf-8"
            )
        )
        expected = set(evidence["members"])
        self.assertEqual(artifact["status"], "RATIFIED_FOR_DASHBOARD")
        self.assertEqual(artifact["counts"], {
            "roster": len(expected), "accepted": len(expected), "missing": 0
        })
        self.assertEqual(set(artifact["briefs"]), expected)
        self.assertTrue(all(
            row.get("ok") is True
            and all(row.get(f"p{index}") for index in range(1, 4))
            and 200 <= row.get("words", 0) <= 250
            for row in artifact["briefs"].values()
        ))
        producer = (FEC / "surfaces" / "make_member_desk.py").read_text(
            encoding="utf-8"
        )
        template = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        integrity = (FEC / "audit" / "dashboard_integrity.py").read_text(
            encoding="utf-8"
        )
        wrapper = (
            FEC / "legislation" / "run_legislation_dashboard_member_desk.py"
        ).read_text(encoding="utf-8")
        for source in (producer, integrity, wrapper):
            self.assertIn("data/district/district_briefs_v1.json", source)
        self.assertIn("'district_brief': {", producer)
        self.assertIn("'district_brief'", producer.split("HEAVY_MEMBER_FIELDS", 1)[1])
        self.assertIn("class=districtbrief", template)
        self.assertIn("esc(brief.p1)", template)
        self.assertIn("esc(brief.p2)", template)
        self.assertIn("esc(brief.p3)", template)

    def test_personal_texture_uses_the_dedicated_display_gate(self) -> None:
        template = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "allItems.filter(x=>x.display_eligible===true)",
            template,
        )
        self.assertIn(
            ".filter(a=>a.narrative_eligible===true).forEach(a=>{",
            template,
        )
        self.assertIn("speaker-verified member details", template)
        self.assertIn("personal-detail precision gate", template)
        self.assertIn("personalClaimInBiography", template)
        self.assertIn("biography duplicate", template)

    def test_member_bio_uses_current_roster_and_renders_wikipedia_summary(self) -> None:
        producer = (FEC / "surfaces" / "make_member_desk.py").read_text(encoding="utf-8")
        template = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn("or _ROSTER_BIO.get(name)", producer)
        self.assertIn("member_wikipedia_summaries.json", producer)
        self.assertIn("Early life & education", template)
        self.assertIn("Family & personal life", template)
        self.assertIn("Facebook", template)

    def test_media_footprint_is_uncapped_and_client_paginated(self) -> None:
        producer = (FEC / "surfaces" / "make_member_desk.py").read_text(encoding="utf-8")
        template = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn("MEDIA_CAP = 0", producer)
        self.assertNotIn("os.environ.get('DESK_MEDIA_CAP'", producer)
        self.assertIn("Washington Journal", producer)
        self.assertIn("MSNBC / MS NOW", producer)
        self.assertIn("iv_sources", producer)
        self.assertIn("Show 50 more", template)
        self.assertIn("networkOpts", template)
        self.assertIn("Coverage included:", template)
        self.assertIn("All ${outletRows.length} interview source", template)
        self.assertIn("All ${pressRows.length} press publisher", template)
        self.assertIn(
            "Press sentiment is active at a diminished 10% overall weight",
            template,
        )

    def test_dashboard_exposes_curve_and_cu_metric_provenance(self) -> None:
        desk = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        network = (
            FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl"
        ).read_text(encoding="utf-8")
        metrics = (FEC / "district" / "district_cu_metrics.py").read_text(
            encoding="utf-8"
        )
        for token in (
            "Industry Standout: credit-union jobs",
            "Growth quotient",
            "Shift-share analysis",
            "NAICS 52",
        ):
            self.assertIn(token, desk)
        self.assertIn("no CU-only history is fabricated", metrics)
        self.assertIn("curved labels", network)
        self.assertIn("sign-preserving curved label", network)
        self.assertIn("Senate population normalization", desk)
        self.assertIn("Britannica seat table", desk)


if __name__ == "__main__":
    unittest.main()
