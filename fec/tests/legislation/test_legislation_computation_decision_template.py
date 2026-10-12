import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = (
    ROOT
    / "audits"
    / "legfix5"
    / "manual_inputs"
    / "legislation_computation_decisions.required.json"
)


class LegislationComputationDecisionTemplateTests(unittest.TestCase):
    def test_template_is_fact_bound_and_contains_no_approval(self) -> None:
        value = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(
            value["schema"], "legislation-computation-decisions-required-v2"
        )
        self.assertEqual(value["status"], "PENDING_USER_DECISION")
        self.assertFalse(value["production_or_mainframe_promotion_allowed"])
        self.assertFalse(value["rank_recalculation_claim_allowed"])

        binding = value["factual_bindings"]
        layout = ROOT / binding["audited_layout_path"]
        self.assertEqual(
            hashlib.sha256(layout.read_bytes()).hexdigest(),
            binding["audited_layout_sha256"],
        )

        self.assertFalse(value["role_weight_policy"]["human_approved"])
        self.assertIsNone(
            value["role_weight_policy"]["selected_role_weight_policy"]
        )
        self.assertFalse(value["congress_decay_policy"]["approved_by_user"])
        self.assertIsNone(value["congress_decay_policy"]["selected_schedule"])
        self.assertFalse(value["legislative_efficiency"]["approved_by_user"])
        self.assertFalse(
            value["special_numeric_semantics"][
                "cfpb_accepted_bill_activity_routing"
            ]["approved_by_user"]
        )
        self.assertFalse(value["press_member_alignment"]["approved_by_user"])

    def test_current_gate_incompatibilities_are_explicit(self) -> None:
        value = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(
            value["legislative_efficiency"]["current_gate_compatibility"][
                "status"
            ],
            "GATE_SCHEMA_HARDENED_DECISION_STILL_REQUIRED",
        )
        self.assertEqual(
            value["special_numeric_semantics"][
                "cfpb_accepted_bill_activity_routing"
            ]["current_gate_compatibility"]["status"],
            "TYPED_GATE_HARDENED_ROUTE_IMPLEMENTATION_PENDING_SELECTION",
        )
        self.assertEqual(
            value["press_member_alignment"]["current_gate_compatibility"][
                "status"
            ],
            "GATE_SCHEMA_HARDENED_DECISION_STILL_REQUIRED",
        )


if __name__ == "__main__":
    unittest.main()
