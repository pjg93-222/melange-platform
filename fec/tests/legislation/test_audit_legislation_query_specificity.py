from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fec.audit import audit_legislation_query_specificity as AUDIT


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture() -> dict:
    categories = []
    probes = []
    for index in range(19):
        category_id = f"cat_{index}"
        title = "SMART Act" if index == 0 else None
        specific = "Specific Credit Union Modernization Act" if index == 1 else None
        aliases = [value for value in (title, specific) if value]
        terms = []
        if specific:
            terms.append({"kind": "known_title", "query": f'"{specific}"'})
        categories.append(
            {
                "id": category_id,
                "title_aliases": aliases,
                "query_terms": terms,
            }
        )
        if title:
            probes.append(
                {
                    "id": "probe_smart",
                    "parent_category_id": category_id,
                    "title_alias": title,
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "required_for_acceptance": True,
                    "membership_non_dispositive": True,
                    "ambiguous_short_act_alias": True,
                    "query_terms": [
                        {
                            "kind": "ambiguous_title_alias_probe",
                            "query": '"SMART Act"',
                        }
                    ],
                    "advanced_search_query": '("SMART Act")',
                }
            )
        if specific:
            probes.append(
                {
                    "id": "probe_specific",
                    "parent_category_id": category_id,
                    "title_alias": specific,
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "required_for_acceptance": True,
                    "membership_non_dispositive": True,
                    "ambiguous_short_act_alias": False,
                    "query_terms": [
                        {
                            "kind": "title_alias_probe",
                            "query": f'"{specific}"',
                        }
                    ],
                    "advanced_search_query": f'("{specific}")',
                }
            )
    while len(probes) < 43:
        probes.append(
            {
                "id": f"recall_{len(probes)}",
                "scoring_category": False,
                "record_kind": "coverage_probe",
                "required_for_acceptance": True,
                "query_terms": [{"kind": "coverage_probe", "query": '"credit union"'}],
            }
        )
    return {
        "collection_contract": {"quorum_used": False},
        "categories": categories,
        "coverage_probes": probes,
    }


class QuerySpecificityAuditTests(unittest.TestCase):
    def test_valid_registry_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            write(path, fixture())
            result = AUDIT.audit(path)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["counts"]["required_queries"], 62)
            self.assertEqual(
                result["counts"]["ambiguous_aliases_in_category_queries"], 0
            )

    def test_ambiguous_alias_leak_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            value = fixture()
            value["categories"][0]["query_terms"].append(
                {"kind": "known_title", "query": '"SMART Act"'}
            )
            write(path, value)
            result = AUDIT.audit(path)
            self.assertEqual(result["status"], "BLOCKED")
            codes = {row["code"] for row in result["blockers"]}
            self.assertIn("ambiguous_act_leaked_into_scoring_query", codes)
            self.assertIn("generic_or_ambiguous_act_in_category_query", codes)

    def test_dispositive_title_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            value = fixture()
            value["coverage_probes"][0]["membership_non_dispositive"] = False
            write(path, value)
            result = AUDIT.audit(path)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIn(
                "title_probe_contract_drift",
                {row["code"] for row in result["blockers"]},
            )


if __name__ == "__main__":
    unittest.main()
