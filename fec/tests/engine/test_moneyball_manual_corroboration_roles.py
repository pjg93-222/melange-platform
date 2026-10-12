from __future__ import annotations

import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import moneyball_core as MC  # noqa: E402


class EmptySheet:
    def iter_rows(self, *, min_row: int, values_only: bool):
        assert min_row == 2
        assert values_only is True
        return iter(())


class EmptyRoleWorkbook:
    sheetnames = [
        "Sponsor Interactions",
        "Original Co-Sponsor Interaction",
    ]

    def __getitem__(self, name: str):
        if name not in self.sheetnames:
            raise KeyError(name)
        return EmptySheet()

    def close(self) -> None:
        return None


class ManualCorroborationRoleTests(unittest.TestCase):
    def tearDown(self) -> None:
        MC._CORR = None

    def test_exact_normalized_role_semantics(self) -> None:
        cases = {
            "Sponsor": "sponsor",
            " sponsor ": "sponsor",
            "Original Cosponsor": "original-cosponsor",
            "original-cosponsor": "original-cosponsor",
            "ORIGINAL CO-SPONSOR": "original-cosponsor",
            "Cosponsor": "cosponsor",
            "co-sponsor": "cosponsor",
            "later cosponsor": "other",
            "sponsorship": "other",
            "": "other",
        }
        self.assertEqual(
            {
                value: MC.manual_corroboration_role(value)
                for value in cases
            },
            cases,
        )

    def test_later_cosponsor_never_enters_authored_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manual = root / "manual_bill_evidence.csv"
            with manual.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "Bill",
                        "Congress",
                        "Member",
                        "Role",
                        "Category",
                        "LastAction",
                        "Title",
                        "Summary",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "Bill": "LTR-SPONSOR",
                            "Congress": "119",
                            "Member": "Direct Sponsor",
                            "Role": "Sponsor",
                            "Category": "Payments",
                            "LastAction": "Became Law",
                        },
                        {
                            "Bill": "LTR-ORIGINAL",
                            "Congress": "119",
                            "Member": "Original Member",
                            "Role": "Original Co-Sponsor",
                            "Category": "CFPB",
                            "LastAction": "Passed House",
                        },
                        {
                            "Bill": "LTR-LATER",
                            "Congress": "119",
                            "Member": "Later Member",
                            "Role": "Cosponsor",
                            "Category": "Interchange",
                            "LastAction": "Became Law",
                        },
                    ]
                )

            fake_openpyxl = SimpleNamespace(
                load_workbook=lambda *args, **kwargs: EmptyRoleWorkbook()
            )
            roster = {
                "Direct Sponsor": {},
                "Original Member": {},
                "Later Member": {},
            }
            MC._CORR = None
            with (
                mock.patch.dict(
                    sys.modules, {"openpyxl": fake_openpyxl}
                ),
                mock.patch.object(MC, "congestion", return_value=roster),
                mock.patch.object(
                    MC, "p", side_effect=lambda *parts: str(root.joinpath(*parts))
                ),
            ):
                result = MC.bill_corroboration()

            self.assertEqual(
                result["Direct Sponsor"]["sponsored"], {"Payments"}
            )
            self.assertEqual(
                result["Direct Sponsor"]["advanced"], {"Payments"}
            )
            self.assertEqual(
                result["Direct Sponsor"]["direct_sponsored_bills"],
                {("LTR-SPONSOR", "119")},
            )
            self.assertEqual(
                result["Original Member"]["sponsored"], {"CFPB"}
            )
            self.assertEqual(
                result["Original Member"]["advanced"], {"CFPB"}
            )
            self.assertEqual(
                result["Original Member"]["direct_sponsored_bills"], set()
            )
            self.assertNotIn("Later Member", result)


if __name__ == "__main__":
    unittest.main()
