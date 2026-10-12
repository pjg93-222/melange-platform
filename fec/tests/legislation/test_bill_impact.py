"""Meaningful frequency, content and historical-cutoff contracts."""
from copy import deepcopy
import unittest

from fec.legislation.build_bill_impact import (
    FrequencyPolicy, build_impacts, classify_content, frequency_for_bill,
    publication_registry,
)


def bill(**changes):
    row = {"measure_id": "118:hr:1", "measure_type": "hr", "introduced_date": "2023-01-01", "title": "Financial Service Access Act", "summary": "This bill changes financial regulation."}
    row.update(changes)
    return row


def document(url="https://example.org/article", text="H.R. 1 is important", **changes):
    row = {"url": url, "text": text, "published_date": "2023-01-10", "organization_family": "acu", "source_kind": "trade_association", "sector": "credit_union", "full_text_complete": True,
           "bill_links": [{"measure_id": "118:hr:1", "verified": True, "basis": "explicit_congress_and_bill_citation", "literal_occurrences": 1}]}
    row.update(changes)
    return row


def complete_coverage(*organizations):
    return [{"organization_family": org, "start_date": "2023-01-01", "end_date": "2023-12-31", "inventory_complete": True, "bill_reference_search_complete": True} for org in organizations]


def policy(panel=("acu",), **changes):
    defaults = dict(profile="sector_frequency", panel=panel, min_publications=2, min_frequency=0.1, min_exposure_per_organization=1)
    defaults.update(changes)
    return FrequencyPolicy(**defaults)


class BillImpactTests(unittest.TestCase):
    def test_supportive_critical_neutral_have_identical_significance(self):
        outcomes = []
        for sentiment in ("supportive", "critical", "neutral"):
            docs = [document(text=f"{sentiment}: analysis of H.R. 1", sentiment=sentiment), document("https://example.org/new", text=f"Second {sentiment} report on H.R. 1", sentiment=sentiment)]
            registry = publication_registry(docs, as_of="2023-12-31")
            result = build_impacts([bill()], registry, as_of="2023-12-31", policy=policy(), coverage=complete_coverage("acu"))[0]
            outcomes.append((result["class"], result["weight"], result["sector_significance"]["M"], result["sector_significance"]["F"]))
        self.assertEqual(outcomes, [("SS", 10, 2, 1.0)] * 3)

    def test_member_fragments_repeated_strings_and_html_pdf_copies_count_once(self):
        docs = [document("https://www.example.org/story/?utm_source=test", text="H.R. 1 H.R. 1 H.R. 1", full_text_complete=False, origin_document_id="original:10"),
                document("https://example.org/story?output=print", text="Member Jones said H.R. 1", full_text_complete=False, origin_document_id="original:10"),
                document("https://example.org/story.pdf", text="PDF text on H.R. 1", origin_document_id="original:10")]
        registry = publication_registry(docs, as_of="2023-12-31")
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry[0]["fragment_count"], 3)
        freq = frequency_for_bill(bill(), registry, policy(), as_of="2023-12-31", coverage=complete_coverage("acu"))
        self.assertEqual((freq["M"], freq["U"], freq["exposure_by_organization"]["acu"]), (1, 1, 1))

    def test_syndicated_copy_does_not_add_publisher_breadth(self):
        original = document(syndication_group="syndicated:story")
        copied = document("https://other.org/copy", text="Reprint of H.R. 1 story", syndication_group="syndicated:story", organization_family="another_association", is_republication=True, origin_organization="acu", origin_kind="trade_association")
        registry = publication_registry([original, copied], as_of="2023-12-31")
        freq = frequency_for_bill(bill(), registry, policy(), as_of="2023-12-31", coverage=complete_coverage("acu"))
        self.assertEqual((freq["M"], freq["U"]), (1, 1))

    def test_genuinely_new_publication_counts_and_frequency_balances_sources(self):
        docs = [document(), document("https://example.org/new", text="A genuinely new analysis of H.R. 1"),
                document("https://second.org/story", text="Second organization H.R. 1 analysis", organization_family="second"),
                document("https://second.org/other", text="Unrelated publication", organization_family="second", bill_links=[])]
        registry = publication_registry(docs, as_of="2023-12-31")
        freq = frequency_for_bill(bill(), registry, policy(("acu", "second")), as_of="2023-12-31", coverage=complete_coverage("acu", "second"))
        self.assertEqual((freq["M"], freq["U"]), (3, 2))
        self.assertEqual(freq["F"], 0.75)

    def test_missing_panel_coverage_never_means_measured_zero_or_ss(self):
        registry = publication_registry([document()], as_of="2023-12-31")
        freq = frequency_for_bill(bill(), registry, policy(("acu", "missing")), as_of="2023-12-31", coverage=complete_coverage("acu"))
        self.assertIsNone(freq["F"])
        self.assertEqual(freq["zero_interpretation"], "not_evidence_of_absence")
        result = build_impacts([bill()], registry, as_of="2023-12-31", policy=policy(min_publications=1))[0]
        self.assertEqual(result["class"], "S")

    def test_future_version_cannot_supply_earlier_origin_links_or_completeness(self):
        early = document(text="Accessible member fragment", full_text_complete=False, bill_links=[], origin_document_id="one")
        future = document(text="Future full body mentions H.R. 1", published_date="2023-06-01", origin_document_id="one", source_kind="independent_news", organization_family="future_news")
        registry = publication_registry([early, future], as_of="2023-02-01")
        available = [r for r in registry if r["count_eligible"]]
        self.assertEqual(len(available), 1)
        self.assertEqual(available[0]["organization_family"], "acu")
        self.assertFalse(available[0]["full_text_complete"])
        self.assertEqual(available[0]["bill_links"], [])
        self.assertEqual([r["exclusion_reason"] for r in registry if not r["count_eligible"]], ["future_publication"])

    def test_later_added_reference_does_not_leak_into_early_age_window(self):
        original = document(bill_links=[], full_text_complete=False, origin_document_id="same")
        later = document(text="Later updated fragment H.R. 1", published_date="2023-06-01", full_text_complete=False, origin_document_id="same")
        registry = publication_registry([original, later], as_of="2023-12-31")
        first30 = frequency_for_bill(bill(), registry, policy(), as_of="2023-12-31", days=30)
        lifetime = frequency_for_bill(bill(), registry, policy(), as_of="2023-12-31")
        self.assertEqual(first30["M"], 0)
        self.assertEqual(lifetime["M"], 1)
        self.assertEqual(registry[0]["bill_links"][0]["mention_date"], "2023-06-01")

    def test_explicit_future_observation_and_link_observation_censored(self):
        future_observation = document(observed_date="2023-05-01")
        linked_later = document("https://example.org/other", bill_links=[{"measure_id": "118:hr:1", "verified": True, "basis": "verified later", "observed_date": "2023-05-01"}])
        registry = publication_registry([future_observation, linked_later], as_of="2023-02-01")
        self.assertTrue(any(r["exclusion_reason"] == "future_observation" for r in registry))
        self.assertTrue(all(not r["bill_links"] for r in registry))

    def test_unverified_naked_bill_reference_cannot_link_cross_congress(self):
        registry = publication_registry([document(bill_links=[{"measure_id": "hr1", "verified": True, "basis": "naked number"}, {"measure_id": "117:hr:1", "verified": True, "basis": "explicit past congress"}])], as_of="2023-12-31")
        freq = frequency_for_bill(bill(), registry, policy(), as_of="2023-12-31")
        self.assertEqual(freq["M"], 0)
        self.assertEqual(len(registry[0]["unverified_bill_links"]), 1)

    def test_congressional_self_publication_never_establishes_independent_news(self):
        registry = publication_registry([document(source_kind="congressional_office", organization_family="office")], as_of="2023-12-31")
        self.assertFalse(registry[0]["count_eligible"])
        result = build_impacts([bill()], registry, as_of="2023-12-31", policy=FrequencyPolicy(profile="independent_news", panel=("office",)))[0]
        self.assertEqual(result["class"], "S")

    def test_class_weights_naming_mixed_policy_letters_and_candidate_counts(self):
        naming = bill(title='To designate the post office building as the "Ada Smith Post Office".', summary="This bill designates the named post office building.")
        self.assertEqual(classify_content(naming)[0], "C")
        mixed = bill(title="To establish a grant program commemorating national service.", summary="Creates eligibility for federal grants.")
        self.assertEqual(classify_content(mixed)[0], "S")
        letter = bill(measure_type="letter", candidate_features={"unique_cosponsor_count": 1000, "unique_amendment_count": 500})
        rows = build_impacts([naming, mixed, letter], as_of="2023-12-31")
        self.assertEqual([r["weight"] for r in rows], [1, 5, 5])
        self.assertTrue(all(r["candidate_features_affect_weight"] is False for r in rows))
        self.assertTrue(classify_content(bill(title=naming["title"], summary=""))[2])

    def test_latest_review_before_cutoff_wins_independently_of_input_order(self):
        first = {"measure_id": "118:hr:1", "reviewed_date": "2023-02-01", "class": "C", "reviewer": "test", "evidence": "Reviewed source"}
        later = dict(first, reviewed_date="2023-03-01", **{"class": "S"})
        future = dict(first, reviewed_date="2023-05-01", **{"class": "SS"})
        rows = build_impacts([bill()], as_of="2023-04-01", reviews=[later, first, future])
        self.assertEqual(rows[0]["class"], "S")


if __name__ == "__main__":
    unittest.main()
