from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "legislation"
    / "collect_govinfo_bill_xml_versions.py"
)
SPEC = importlib.util.spec_from_file_location("govinfo_version_collector", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def version_url(canonical_key: str, version_code: str) -> str:
    congress, measure_type, number = canonical_key.split(":")
    package = f"BILLS-{congress}{measure_type}{number}{version_code}"
    return (
        f"https://www.govinfo.gov/content/pkg/{package}/xml/{package}.xml"
    )


def source_record(
    canonical_key: str, versions: list[dict[str, object]]
) -> dict[str, object]:
    congress, measure_type, number = canonical_key.split(":")
    source_url = (
        "https://www.govinfo.gov/bulkdata/BILLSTATUS/"
        f"{congress}/{measure_type}/BILLSTATUS-{congress}{measure_type}{number}.xml"
    )
    return {
        "schema": "test",
        "canonical_key": canonical_key,
        "identity": {
            "congress": int(congress),
            "type": measure_type,
            "number": int(number),
        },
        "text_versions": versions,
        "provenance": {
            "source_url": source_url,
            "source_sha256": hashlib.sha256(source_url.encode()).hexdigest(),
        },
    }


def bill_xml(canonical_key: str) -> bytes:
    congress, measure_type, number = canonical_key.split(":")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<bill><form>"
        f"<congress>{congress}th Congress</congress>"
        f"<legis-num>{measure_type.upper()}. {number}</legis-num>"
        "</form></bill>"
    ).encode()


def bills_uslm_url(canonical_key: str, version_code: str) -> str:
    return version_url(canonical_key, version_code).replace("/xml/", "/uslm/")


def plaw_url(congress: str, law_type: str, law_number: str, format_: str = "uslm") -> str:
    package = f"PLAW-{congress}{law_type}{law_number}"
    return f"https://www.govinfo.gov/content/pkg/{package}/{format_}/{package}.xml"


def bill_uslm(canonical_key: str) -> bytes:
    congress, measure_type, number = canonical_key.split(":")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE bill PUBLIC "-//US Congress//DTD USLM 1.0//EN" '
        '"https://xml.house.gov/schemas/uslm/1.0/USLM-1.0.dtd">'
        f'<bill><meta><congress>{congress}</congress>'
        f'<citableAs>{measure_type.upper()}. {number}</citableAs>'
        '</meta></bill>'
    ).encode()


def legacy_bill_xml(
    canonical_key: str,
    *,
    root_name: str = "bill",
    public_id: str = "-//US Congress//DTDs/bill.dtd//EN",
    system_id: str = "bill.dtd",
    congress_text: str | None = None,
) -> bytes:
    congress, measure_type, number = canonical_key.split(":")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE {root_name} PUBLIC "{public_id}" "{system_id}">'
        f"<{root_name}><form>"
        f"<congress>{congress_text or congress + 'th CONGRESS'}</congress>"
        f"<legis-num>{measure_type.upper()}. {number}</legis-num>"
        f"</form></{root_name}>"
    ).encode()


def engrossed_amendment_xml(
    canonical_key: str,
    version_code: str,
    *,
    title_identity: str | None = None,
    congress_text: str | None = None,
) -> bytes:
    """Mirror a real GovInfo engrossed-amendment document.

    ``amend.dtd`` carries no <legis-num>; identity lives in the Dublin Core
    title, e.g. "116 S178 EAH: ..." or "117 HR 1437 EAH: ...".
    """

    congress, measure_type, number = canonical_key.split(":")
    identity = title_identity or (
        f"{congress} {measure_type.upper()}{number} {version_code.upper()}"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE amendment-doc PUBLIC "-//US Congress//DTDs/amend.dtd//EN" '
        '"amend.dtd">'
        '<amendment-doc amend-type="engrossed-amendment">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dublinCore>'
        f"<dc:title>{identity}: An Act to do a thing</dc:title>"
        "</dublinCore></metadata>"
        "<engrossed-amendment-form>"
        f"<congress>{congress_text or congress + 'th CONGRESS'}</congress>"
        "</engrossed-amendment-form>"
        "</amendment-doc>"
    ).encode()


def public_law_uslm(canonical_key: str, law_number: str) -> bytes:
    congress, measure_type, number = canonical_key.split(":")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<law><meta><congress>{congress}</congress>'
        '<publicPrivate>Public Law</publicPrivate>'
        f'<docNumber>{law_number}</docNumber>'
        f'<ref href="/us/bill/{congress}/{measure_type}/{number}" />'
        '</meta></law>'
    ).encode()


class GovInfoCollectorTests(unittest.TestCase):
    def write_record(
        self,
        root: pathlib.Path,
        name: str,
        record: dict[str, object],
    ) -> None:
        (root / name).write_text(json.dumps(record), encoding="utf-8")

    def test_plan_binds_all_versions_and_preserves_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "a.json",
                source_record(
                    "118:hr:42",
                    [
                        {
                            "date": "2023-01-09T05:00:00Z",
                            "type": "Introduced in House",
                            "urls": [version_url("118:hr:42", "ih")],
                        },
                        {
                            "date": "2023-03-01T05:00:00Z",
                            "type": "Engrossed in House",
                            "urls": [version_url("118:hr:42", "eh")],
                        },
                    ],
                ),
            )
            self.write_record(
                root, "b.json", source_record("118:s:7", [])
            )
            plan = collector.build_plan(root)

            self.assertEqual(len(plan.records), 2)
            self.assertEqual(len(plan.jobs), 2)
            self.assertEqual(
                {job["version_code"] for job in plan.jobs}, {"ih", "eh"}
            )
            self.assertEqual(len(plan.missing), 1)
            self.assertEqual(plan.missing[0]["canonical_key"], "118:s:7")
            self.assertEqual(plan.missing[0]["reason"], "no_text_versions")
            self.assertTrue(
                all(len(str(job["job_id"])) == 64 for job in plan.jobs)
            )

    def test_source_package_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            record = source_record(
                "118:hr:42",
                [{"urls": [version_url("118:hr:42", "ih")]}],
            )
            provenance = record["provenance"]
            assert isinstance(provenance, dict)
            provenance["source_url"] = str(provenance["source_url"]).replace(
                "118hr42", "118hr43"
            )
            self.write_record(root, "record.json", record)
            with self.assertRaisesRegex(
                collector.CollectionError, "source-package identity mismatch"
            ):
                collector.build_plan(root)

    def test_version_url_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "record.json",
                source_record(
                    "118:hr:42",
                    [{"urls": [version_url("118:hr:43", "ih")]}],
                ),
            )
            with self.assertRaisesRegex(
                collector.CollectionError, "version URL identity mismatch"
            ):
                collector.build_plan(root)

    def test_xml_identity_rejects_html_and_wrong_measure(self) -> None:
        job = {"canonical_key": "118:hr:42"}
        accepted = collector.validate_xml(bill_xml("118:hr:42"), job)
        self.assertEqual(accepted["matched_measure"], "hr42")
        with self.assertRaisesRegex(collector.CollectionError, "HTML"):
            collector.validate_xml(
                b"<html><body>challenge</body></html>" + b" " * 64, job
            )
        with self.assertRaisesRegex(
            collector.CollectionError, "measure identity mismatch"
        ):
            collector.validate_xml(bill_xml("118:hr:43"), job)

    def test_bills_uslm_plan_and_doctype_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "record.json",
                source_record(
                    "119:s:216",
                    [{"type": "Enrolled Bill", "urls": [bills_uslm_url("119:s:216", "enr")]}],
                ),
            )
            job = collector.build_plan(root).jobs[0]
            self.assertEqual(job["govinfo_surface"], "BILLS")
            self.assertEqual(job["document_format"], "uslm")
            accepted = collector.validate_xml(bill_uslm("119:s:216"), job)
            self.assertEqual(accepted["matched_measure"], "s216")
            with self.assertRaisesRegex(collector.CollectionError, "ENTITY"):
                collector.validate_xml(
                    bill_uslm("119:s:216").replace(
                        b"<bill>", b"<!ENTITY x SYSTEM 'file:///x'><bill>"
                    ),
                    job,
                )

    def test_observed_resolution_and_amendment_doctypes_are_exactly_allowed(self) -> None:
        resolution_job = {"canonical_key": "116:hconres:100"}
        resolution = legacy_bill_xml(
            "116:hconres:100",
            root_name="resolution",
            public_id="-//US Congress//DTDs/res.dtd//EN",
            system_id="res.dtd",
        )
        self.assertEqual(
            collector.validate_xml(resolution, resolution_job)["matched_measure"],
            "hconres100",
        )
        amendment_job = {"canonical_key": "117:hr:7108"}
        amendment = legacy_bill_xml(
            "117:hr:7108",
            root_name="amendment-doc",
            public_id="-//US Congress//DTDs/amend.dtd//EN",
            system_id="amend.dtd",
        )
        self.assertEqual(
            collector.validate_xml(amendment, amendment_job)["matched_measure"],
            "hr7108",
        )
        with self.assertRaisesRegex(collector.CollectionError, "unapproved DOCTYPE"):
            collector.validate_xml(
                resolution.replace(b'"res.dtd"', b'"bill.dtd"'),
                resolution_job,
            )

    def test_engrossed_amendment_identity_comes_from_dublin_core_title(
        self,
    ) -> None:
        # Real amend.dtd documents carry no <legis-num>; identity must bind
        # congress, measure, and version together from the Dublin Core title.
        for key, version, spacing in (
            ("116:s:178", "eah", "116 S178 EAH"),
            ("117:hr:1437", "eah", "117 HR 1437 EAH"),
            ("116:s:1069", "eah", "116 S1069 EAH"),
        ):
            job = {"canonical_key": key, "version_code": version}
            accepted = collector.validate_xml(
                engrossed_amendment_xml(key, version, title_identity=spacing),
                job,
            )
            self.assertEqual(accepted["identity_source"], "dublin_core_title")
            self.assertEqual(accepted["xml_root"], "amendment-doc")
            congress, measure_type, number = key.split(":")
            self.assertEqual(
                accepted["matched_amendment_identity"],
                f"{congress}{measure_type}{number}{version}",
            )

    def test_engrossed_amendment_identity_rejects_wrong_bindings(self) -> None:
        key, version = "116:s:178", "eah"
        job = {"canonical_key": key, "version_code": version}

        # Wrong measure number.
        with self.assertRaisesRegex(
            collector.CollectionError, "amendment-doc identity mismatch"
        ):
            collector.validate_xml(
                engrossed_amendment_xml(
                    key, version, title_identity="116 S179 EAH"
                ),
                job,
            )
        # Wrong version code: the same measure, a different document.
        with self.assertRaisesRegex(
            collector.CollectionError, "amendment-doc identity mismatch"
        ):
            collector.validate_xml(
                engrossed_amendment_xml(
                    key, version, title_identity="116 S178 EAS"
                ),
                job,
            )
        # Wrong Congress is refused before the title is ever consulted.
        with self.assertRaisesRegex(
            collector.CollectionError, "Congress identity mismatch"
        ):
            collector.validate_xml(
                engrossed_amendment_xml(
                    key,
                    version,
                    title_identity="115 S178 EAH",
                    congress_text="115th CONGRESS",
                ),
                job,
            )
        # An unparsable or absent title must fail closed, never default open.
        with self.assertRaisesRegex(
            collector.CollectionError, "title identity unparsable"
        ):
            collector.validate_xml(
                engrossed_amendment_xml(
                    key, version, title_identity="Uighur Intervention Act"
                ),
                job,
            )
        with self.assertRaisesRegex(
            collector.CollectionError, "missing its Dublin Core title"
        ):
            collector.validate_xml(
                engrossed_amendment_xml(key, version).replace(
                    b"<dc:title>116 S178 EAH: An Act to do a thing</dc:title>",
                    b"",
                ),
                job,
            )

    def test_enrolled_bill_worded_congress_identity_is_exactly_allowed(self) -> None:
        job = {"canonical_key": "116:s:3312"}
        enrolled = legacy_bill_xml(
            "116:s:3312",
            congress_text=(
                "One Hundred Sixteenth Congress of the United States of America"
            ),
        )
        self.assertEqual(
            collector.validate_xml(enrolled, job)["matched_congress"], "116"
        )
        with self.assertRaisesRegex(collector.CollectionError, "Congress identity mismatch"):
            collector.validate_xml(
                enrolled.replace(b"Sixteenth", b"Seventeenth"), job
            )

    def test_public_law_binds_originating_bill_and_rejects_wrong_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "record.json",
                source_record(
                    "116:hr:3311",
                    [{"type": "Public Law", "urls": [plaw_url("116", "publ", "54")]}],
                ),
            )
            job = collector.build_plan(root).jobs[0]
            self.assertEqual(job["govinfo_surface"], "PLAW")
            self.assertEqual(job["law_number"], "54")
            accepted = collector.validate_xml(public_law_uslm("116:hr:3311", "54"), job)
            self.assertEqual(accepted["matched_originating_bill_ref"], "/us/bill/116/hr/3311")
            wrong = public_law_uslm("116:hr:3311", "54").replace(b"/3311", b"/3312")
            with self.assertRaisesRegex(collector.CollectionError, "originating-bill"):
                collector.validate_xml(wrong, job)

    def test_public_law_requires_public_private_version_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "record.json",
                source_record(
                    "116:hr:3311",
                    [{"type": "Enrolled Bill", "urls": [plaw_url("116", "publ", "54", "xml")]}],
                ),
            )
            with self.assertRaisesRegex(collector.CollectionError, "version type mismatch"):
                collector.build_plan(root)

    def test_accept_job_calls_fetch_once_and_content_addresses_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_record(
                root,
                "record.json",
                source_record(
                    "118:hr:42",
                    [
                        {
                            "date": "2023-01-09T05:00:00Z",
                            "type": "Introduced in House",
                            "urls": [version_url("118:hr:42", "ih")],
                        }
                    ],
                ),
            )
            job = collector.build_plan(root).jobs[0]
            body = bill_xml("118:hr:42")
            calls: list[str] = []

            def fetch(url: str) -> collector.FetchResponse:
                calls.append(url)
                return collector.FetchResponse(
                    body=body,
                    status=200,
                    final_url=url,
                    headers={"content-type": "application/xml"},
                )

            receipt = collector.accept_job(job, root / "objects", fetch)
            self.assertEqual(calls, [job["url"]])
            self.assertEqual(receipt["attempts"], 1)
            self.assertEqual(receipt["retry_waits"], 0)
            self.assertEqual(
                receipt["content_sha256"], hashlib.sha256(body).hexdigest()
            )
            self.assertEqual(
                pathlib.Path(receipt["object_path"]).read_bytes(), body
            )

    def test_immutable_writer_never_replaces_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "receipt.json"
            self.assertTrue(collector.immutable_write(path, b"first"))
            self.assertFalse(collector.immutable_write(path, b"first"))
            with self.assertRaisesRegex(
                collector.CollectionError, "immutable content drift"
            ):
                collector.immutable_write(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_canary_is_deterministic_and_spans_sorted_plan(self) -> None:
        jobs = tuple(
            {"canonical_key": f"118:hr:{index + 1}", "job_id": str(index)}
            for index in range(10)
        )
        selected = collector.select_jobs(jobs, "canary", 3)
        self.assertEqual(
            [job["canonical_key"] for job in selected],
            ["118:hr:1", "118:hr:5", "118:hr:10"],
        )
        self.assertEqual(
            selected, collector.select_jobs(jobs, "canary", 3)
        )

    def test_cli_requires_explicit_plan_canary_or_full(self) -> None:
        with self.assertRaises(SystemExit):
            collector.parse_args(
                ["--input", "in", "--out", "out", "--run-id", "run"]
            )
        args = collector.parse_args(
            [
                "--input",
                "in",
                "--out",
                "out",
                "--run-id",
                "canary-1",
                "--canary",
                "5",
            ]
        )
        self.assertEqual(args.mode, "canary")
        self.assertEqual(args.workers, 16)


if __name__ == "__main__":
    unittest.main()
