"""Synthetic contract tests for the offline package; no research evaluation is rerun."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("staged_predict", Path(__file__).with_name("predict.py"))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def package():
    result = {"schema_version": 1, "kind": "staged_partisan_baseline_package", "live_activation": False,
              "publication_status": "HOLD", "supported_industries": list(p.INDUSTRIES),
              "uncertainty": {"status": "unavailable", "interval": None}, "policy_manifest_sha256": "a" * 64,
              "estimator_version": "partisan-baseline-preview-" + "a" * 12,
              "families": {"House": "persistence", "Senate": "equal_blend"},
              "publication_holds": ["G6 not cleared"], "built_at_utc": "2026-10-06T00:00:00+00:00"}
    result["content_sha256"] = p.sha256(p.canonical(result))
    return result


def request(chamber="Senate"):
    scope = {"kind": "company", "id": "synthetic-company", "industry_id": "commercial_banks",
             "accounting_basis": "employee_two_party_candidate_giving", "member_ids": ["synthetic-company"]}
    anchors = {}
    for name, year, share in (("prior_cycle", 2024, 0.6), ("same_phase", 2022, 0.2)):
        if chamber == "House" and name == "same_phase":
            continue
        anchors[name] = p.seal_anchor({"chamber": chamber, "scope": copy.deepcopy(scope), "cycle": year, "share": share,
            "complete": True, "observed_through": f"{year}-12-31", "available_on": f"{year+1}-01-15",
            "source_id": "synthetic-source", "source_sha256": "b" * 64})
    return {"schema_version": 1, "estimator_version": package()["estimator_version"], "chamber": chamber,
            "target_cycle": 2026, "as_of": "2026-10-05", "scope": scope, "anchors": anchors,
            "expected_anchor_sha256": {k: a["snapshot_sha256"] for k, a in anchors.items()}}


def replace_anchor(req, name, **changes):
    old = req["anchors"][name]
    anchor = {k: v for k, v in old.items() if k != "snapshot_sha256"}
    anchor.update(changes)
    req["anchors"][name] = p.seal_anchor(anchor)
    req["expected_anchor_sha256"][name] = req["anchors"][name]["snapshot_sha256"]


class PredictorTests(unittest.TestCase):
    def test_frozen_rules_complement_and_no_input_mutation(self):
        for chamber, expected in (("House", 0.6), ("Senate", 0.4)):
            model, req = package(), request(chamber)
            before = copy.deepcopy((model, req))
            result = p.project(model, req)
            self.assertAlmostEqual(result["share"]["Republican"], expected)
            self.assertEqual(result["share"]["Republican"] + result["share"]["Democratic"], 1)
            self.assertFalse(result["live_activation"])
            self.assertEqual(result["publication_status"], "HOLD")
            self.assertIn("unvalidated", result["validation_scope"])
            self.assertEqual((model, req), before)

    def test_boundaries_valid_but_bad_missing_and_nonfinite_shares_refused(self):
        self.assertEqual(p.baseline_value("equal_blend", 0, 1), 0.5)
        for bad in (None, True, -0.01, 1.01, float("nan"), float("inf")):
            with self.assertRaises(p.ContractError):
                p.baseline_value("persistence", bad)

    def test_missing_same_phase_not_zero_or_prior_fallback(self):
        req = request()
        req["anchors"].pop("same_phase")
        req["expected_anchor_sha256"].pop("same_phase")
        with self.assertRaisesRegex(p.ContractError, "exactly the anchors"):
            p.project(package(), req)

    def test_wrong_cycle_or_future_availability_rejected(self):
        for changes in ({"cycle": 2020}, {"available_on": "2027-01-01"}, {"complete": False},
                        {"observed_through": "2024-09-30"}, {"available_on": "2024-10-01"}):
            req = request()
            replace_anchor(req, "prior_cycle", **changes)
            with self.assertRaises(p.ContractError):
                p.project(package(), req)

    def test_snapshot_tamper_rejected(self):
        req = request()
        req["anchors"]["prior_cycle"]["share"] = 0.9
        with self.assertRaisesRegex(p.ContractError, "snapshot"):
            p.project(package(), req)

    def test_pinned_hash_chamber_and_membership_must_match(self):
        req = request()
        req["expected_anchor_sha256"]["prior_cycle"] = "0" * 64
        with self.assertRaises(p.ContractError):
            p.project(package(), req)
        req = request()
        replace_anchor(req, "prior_cycle", chamber="House")
        with self.assertRaisesRegex(p.ContractError, "scope mismatch"):
            p.project(package(), req)
        req = request()
        other = dict(req["scope"], member_ids=["different-company"])
        replace_anchor(req, "same_phase", scope=other)
        with self.assertRaises(p.ContractError):
            p.project(package(), req)

    def test_scope_horizon_and_request_identity_rejected(self):
        for key, value in (("as_of", "2024-10-05"), ("as_of", "2026-02-30"), ("target_cycle", 2025),
                           ("chamber", "Both"), ("estimator_version", "another-version")):
            req = request()
            req[key] = value
            with self.assertRaises(p.ContractError):
                p.project(package(), req)
        req = request()
        req["scope"]["industry_id"] = "community_banks"
        with self.assertRaises(p.ContractError):
            p.project(package(), req)

    def test_no_family_override_or_irrelevant_predictor_inputs(self):
        for key, value in (("family", "persistence"), ("control_probability", 0.9)):
            req = request()
            req[key] = value
            with self.assertRaises(p.ContractError):
                p.project(package(), req)

    def test_package_identity_tamper(self):
        model = package()
        model["families"]["Senate"] = "persistence"
        with self.assertRaisesRegex(p.ContractError, "content hash"):
            p.project(model, request())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            raw = p.canonical(package())
            path.write_bytes(raw)
            self.assertEqual(p.load_package(path, p.sha256(raw)), package())
            with self.assertRaisesRegex(p.ContractError, "pinned SHA256"):
                p.load_package(path, "0" * 64)

    def test_activation_tamper_even_with_rehashed_content_rejected(self):
        model = package()
        model.pop("content_sha256")
        model["live_activation"] = True
        model["content_sha256"] = p.sha256(p.canonical(model))
        with self.assertRaisesRegex(p.ContractError, "activation"):
            p.project(model, request())


if __name__ == "__main__":
    unittest.main()
