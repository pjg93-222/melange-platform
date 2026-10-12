"""Public cross-reference snapshots are bounded, read-only and identity checked.

The tab is dwarfed: the published report workbook is the accounting of record,
so this view returns the run's scope and its digest of clear refunds and
evidence-backed contradictions plus unusually large counted receipts, and no
total of any kind. The fixtures below deliberately
keep the producer's full report - summary, partisan table, review queue - so
every test can prove none of it reaches the browser.
"""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import sqlite3
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from cross_reference_data import (DIGEST_MISSING, STEPS, _source_url, cross_reference_for_industry,
                                  latest_cross_reference)

DIGEST = {
    "schema_version": 1, "run_id": "20260910-a0b1", "generated_at": "2026-09-10T15:59:00Z",
    "basis": {"large_cents": 100_000_000, "large_individual_cents": 10_000_000,
              "text": "A receipt is listed as large at $1,000,000.00 or more."},
    "refunds": {
        "linked": [{"refund_record_id": "R1", "original_record_id": "O1", "company": "A Bank",
                    "cycle": 2024, "refund_amount": -2000.0, "original_amount": 50000.0,
                    "refund_source_url": "https://docquery.fec.gov/pdf/333/three.pdf",
                    "original_source_url": "https://docquery.fec.gov/pdf/000/orig.pdf"}],
        "linked_count": 1, "linked_amount": -2000.0,
        "unlinked": [{"record_id": "U1", "company": "A Bank", "cycle": 2024, "amount": -7500.0,
                      "description": "RETURN OF ERRONEOUS DEBIT",
                      "source_url": "https://docquery.fec.gov/pdf/222/two.pdf"}],
        "unlinked_count": 1, "unlinked_amount": -7500.0,
    },
    "outliers": {
        "non_giving": [{"record_id": "N1", "company": "A Bank", "cycle": 2024, "amount": 15000.0,
                        "role": "other_receipt", "description": "LOAN",
                        "source_url": "https://docquery.fec.gov/pdf/111/one.pdf"}],
        "non_giving_count": 1, "non_giving_amount": 15000.0,
        "large": [{"record_id": "L1", "company": "A Bank", "cycle": 2024, "amount": 120000.0,
                   "entity": "IND", "role": "contribution",
                   "source_url": "https://docquery.fec.gov/pdf/444/four.pdf"}],
        "large_count": 1, "large_amount": 120000.0,
    },
    "counts": {"source_records": 5},
}


class CrossReferenceDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "cross-reference"
        self.root.mkdir()
        self.run = self.root / "20260910-a0b1"
        self.run.mkdir()
        self.now = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def fixture(self, report=True, digest=True):
        self.write(self.root / "latest.json", {"schema_version": 1, "run_id": self.run.name})
        self.write(self.run / "progress.json", {
            "schema_version": 1, "run_id": self.run.name,
            "status": "completed" if report else "running", "updated_at": "2026-09-10T15:59:00Z",
            "steps": [{"id": name, "status": "completed" if report else "pending"} for name in STEPS],
        })
        if digest:
            self.write(self.run / "digest.json", json.loads(json.dumps(DIGEST)))
        if report:
            self.write(self.run / "report.json", {
                "schema_version": 1, "run_id": self.run.name, "status": "completed",
                "generated_at": "2026-09-10T15:59:00Z",
                "scope": {"companies": ["A Bank"], "industries": ["Banking"], "cycles": [2024], "source_count": 1},
                "summary": {"input_records": 5, "automatic_decisions": 2, "unresolved_records": 1,
                            "observed_original_giving": 987654.32, "employee_giving": 987654.32,
                            "pac_outgoing": 123456.78, "verified_unique": False},
                "review_queue": [{"company": "A Bank", "reason": "Reference missing", "record_id": "123",
                                  "amount": 40, "donor_name": "Private donor", "contributor_street_1": "12 Private Road",
                                  "raw": {"secret": "omit"}}] * 30,
                "changes": [{"record_id": "456", "company": "A Bank", "before": 160, "after": 100, "delta": -60}] * 30,
                "unresolved_reasons": [{"reason": "Reference missing", "records": 1, "amount": 40}],
                "database": "C:/private/evidence.db", "accuracy": 99.9,
                "partisan_breakdowns": [{"view": "original_giving", "democratic": 80, "republican": 20,
                    "other_unknown": 100, "denominator_dr": 100, "democratic_percent": 80,
                    "republican_percent": 20, "classified_gross_percent": 50,
                    "basis": "Absolute receipt dollars", "raw_donor": "Private donor"}],
                "limitations": ["Collection coverage remains unverified."],
            })

    def view(self):
        return latest_cross_reference(self.root, now=self.now)

    def test_empty_does_not_create_directories(self):
        root = self.root / "never-created"
        result = latest_cross_reference(root)
        self.assertEqual(result["status"], "empty")
        self.assertFalse(root.exists())

    def test_the_payload_is_scope_and_digest_with_no_total_and_no_private_data(self):
        self.fixture()
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        with patch("subprocess.Popen", side_effect=AssertionError("GET must not execute jobs")):
            result = self.view()
        self.assertTrue(result["available"])
        report = result["report"]
        self.assertEqual(set(report), {"run_id", "status", "generated_at", "scope", "digest_available",
                                       "digest", "digest_message", "limitations"})
        self.assertEqual(report["scope"], {"industries": ["Banking"], "companies": ["A Bank"],
                                           "cycles": ["2024"], "company_count": 1})
        self.assertTrue(report["digest_available"])
        self.assertEqual(report["digest_message"], "")
        text, saved = json.dumps(result), json.dumps(report)
        # Negative control: the producer's own totals are in report.json and
        # must not survive the read, by key or by value. The progress envelope
        # keeps its own steps; that is the run's state, not an accounting view.
        for forbidden in ("observed_original_giving", "employee_giving", "pac_outgoing", "summary",
                          "partisan", "review_queue", "unresolved_reasons", "coverage_reasons",
                          "changes", "steps", "source_count"):
            self.assertNotIn(forbidden, saved, forbidden)
        for forbidden in ("observed_original_giving", "partisan", "987654", "123456"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertEqual(len(result["progress"]["steps"]), len(STEPS))
        self.assertNotIn("Private", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("C:/private", text)
        self.assertNotIn("accuracy", text)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.json")})

    def test_the_digest_rows_reach_the_browser_whole(self):
        self.fixture()
        digest = self.view()["report"]["digest"]
        self.assertEqual(digest["basis"], DIGEST["basis"]["text"])
        self.assertEqual(digest["refunds"]["linked"], [{
            "refund_record_id": "R1", "original_record_id": "O1", "company": "A Bank", "cycle": "2024",
            "refund_amount": -2000.0, "original_amount": 50000.0,
            "refund_source_url": "https://docquery.fec.gov/pdf/333/three.pdf",
            "original_source_url": "https://docquery.fec.gov/pdf/000/orig.pdf"}])
        self.assertEqual(digest["refunds"]["unlinked_count"], 1)
        self.assertEqual(digest["refunds"]["unlinked"][0]["description"], "RETURN OF ERRONEOUS DEBIT")
        self.assertEqual(digest["outliers"]["non_giving"][0]["role"], "other_receipt")
        self.assertEqual(digest["outliers"]["non_giving"][0]["amount"], 15000.0)
        self.assertEqual(digest["outliers"]["large"][0]["entity"], "IND")
        self.assertEqual(digest["outliers"]["large_count"], 1)
        # The digest's own cent thresholds stay in the file; the tab reads the sentence.
        self.assertNotIn("large_cents", json.dumps(digest))

    def test_a_run_without_a_digest_says_how_to_build_one(self):
        self.fixture(digest=False)
        report = self.view()["report"]
        self.assertFalse(report["digest_available"])
        self.assertIsNone(report["digest"])
        self.assertEqual(report["digest_message"], DIGEST_MISSING)
        self.assertIn("tools/cross_reference_digest.py", report["digest_message"])

    def test_digest_rows_are_bounded_and_hostile_values_become_unavailable(self):
        self.fixture()
        value = json.loads((self.run / "digest.json").read_text())
        value["refunds"]["unlinked"] = [{
            "record_id": "R" * 400, "company": "A Bank", "cycle": 2024, "amount": "-7500",
            "description": "<img src=x onerror=alert(1)>" + "x" * 1000,
            "source_url": "https://www.fec.gov/file?api_key=secret",
            "donor_name": "Private donor", "raw": {"secret": "omit"}}] * 400
        value["outliers"]["large"] = [{"record_id": "L1", "amount": "120000", "entity": True}]
        value["refunds"]["linked_count"] = -1
        self.write(self.run / "digest.json", value)
        digest = self.view()["report"]["digest"]
        rows = digest["refunds"]["unlinked"]
        self.assertEqual(len(rows), 200)
        self.assertEqual(set(rows[0]), {"record_id", "company", "cycle", "amount", "description", "source_url"})
        self.assertEqual(len(rows[0]["record_id"]), 120)
        self.assertEqual(len(rows[0]["description"]), 300)
        self.assertIsNone(rows[0]["amount"])
        self.assertEqual(rows[0]["source_url"], "")
        self.assertIsNone(digest["outliers"]["large"][0]["amount"])
        self.assertEqual(digest["outliers"]["large"][0]["entity"], "")
        self.assertIsNone(digest["refunds"]["linked_count"])
        self.assertNotIn("Private", json.dumps(digest))
        self.assertNotIn("secret", json.dumps(digest))

    def test_an_oversize_or_invalid_digest_never_reaches_the_browser(self):
        self.fixture()
        for content in ['{"basis":"' + "a" * 4_000_000 + '"}', '{"basis":{},"basis":{}}', '{"x":NaN}', "[]"]:
            (self.run / "digest.json").write_text(content, encoding="utf-8")
            self.assertEqual(self.view()["status"], "error", content[:24])

    def test_running_progress_and_stalled_evidence(self):
        self.fixture(report=False)
        self.assertEqual(self.view()["status"], "running")
        self.assertNotIn("report", self.view())
        self.write(self.run / "progress.json", {"schema_version": 1, "run_id": self.run.name,
                                                "status": "running", "updated_at": "2026-09-08T00:00:00Z"})
        self.assertIn("stale", self.view()["notices"][0])

    def test_no_path_or_run_traversal(self):
        for name in ["../outside", "C:/private", "/tmp/elsewhere", r"..\outside", ".", "a/b", "a\\b"]:
            self.write(self.root / "latest.json", {"schema_version": 1, "run_id": name})
            self.assertEqual(self.view()["status"], "error", name)

    def test_resolved_path_cannot_escape_state_root(self):
        self.fixture()
        resolve = Path.resolve
        target = self.run / "report.json"
        outside = Path(self.temp.name) / "secret.json"
        outside.write_text('{"secret":"should never read"}', encoding="utf-8")
        # Model a symlink/reparse point without requiring Windows symlink privilege.
        def redirected(path, *args, **kwargs):
            return outside if path == target else resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", redirected):
            result = self.view()
        self.assertEqual(result["status"], "error")
        self.assertIn("outside", result["message"])
        self.assertNotIn("secret", json.dumps(result))

    def test_a_redirected_digest_cannot_escape_state_root_either(self):
        self.fixture()
        resolve = Path.resolve
        target = self.run / "digest.json"
        outside = Path(self.temp.name) / "secret-digest.json"
        outside.write_text('{"basis":{"text":"should never read"}}', encoding="utf-8")
        def redirected(path, *args, **kwargs):
            return outside if path == target else resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", redirected):
            result = self.view()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("should never read", json.dumps(result))

    def test_run_mismatch_or_corrupt_fingerprint_hides_the_run(self):
        self.fixture()
        path = self.run / "report.json"
        original = path.read_bytes()
        self.write(self.root / "latest.json", {"schema_version": 1, "run_id": self.run.name,
                                               "report_sha256": hashlib.sha256(original).hexdigest()})
        self.assertTrue(self.view()["available"])
        path.write_bytes(original + b" ")
        self.assertEqual(self.view()["status"], "error")
        self.assertNotIn("report", self.view())
        self.fixture()
        value = json.loads(path.read_text())
        value["run_id"] = "another-run"
        self.write(path, value)
        self.assertEqual(self.view()["status"], "error")

    def test_missing_final_report_is_not_complete(self):
        self.fixture()
        (self.run / "report.json").unlink()
        self.assertEqual(self.view()["status"], "error")

    def test_oversize_duplicate_or_nonfinite_artifacts_are_rejected(self):
        self.fixture()
        for content in ['{"x":"' + 'a' * 2_000_000 + '"}', '{"run_id":"one","run_id":"two"}', '{"amount":NaN}']:
            (self.run / "report.json").write_text(content, encoding="utf-8")
            self.assertEqual(self.view()["status"], "error")

    def test_old_report_notice_is_not_a_source_freshness_claim(self):
        self.fixture()
        path = self.run / "report.json"
        value = json.loads(path.read_text())
        value["generated_at"] = "2026-01-01T00:00:00Z"
        self.write(path, value)
        self.assertIn("generated more than 30 days", self.view()["notices"][0])

    def test_an_industry_selects_its_own_newest_run_and_the_pointer_stays_the_pointer(self):
        self.fixture()
        newer = self.root / "20260911-c2d3"
        newer.mkdir()
        for name in ("report.json", "progress.json", "digest.json"):
            (newer / name).write_bytes((self.run / name).read_bytes())
        for path in (newer / "report.json", newer / "progress.json"):
            value = json.loads(path.read_text())
            value["run_id"] = newer.name
            if "scope" in value:
                value["scope"]["industries"] = ["Credit Union"]
            self.write(path, value)
        pointer = self.view()
        self.assertEqual((pointer["selected_by"], pointer["run_id"], pointer["industry"]),
                         ("pointer", self.run.name, "Banking"))
        union = cross_reference_for_industry(self.root, "Credit Union", now=self.now)
        self.assertEqual((union["selected_by"], union["run_id"], union["industry"]),
                         ("industry", newer.name, "Credit Union"))
        self.assertTrue(union["report"]["digest_available"])
        banking = cross_reference_for_industry(self.root, "Banking", now=self.now)
        self.assertEqual((banking["selected_by"], banking["run_id"]), ("industry", self.run.name))
        missing = cross_reference_for_industry(self.root, "Insurance", now=self.now)
        self.assertEqual((missing["status"], missing["available"], missing["industry"]),
                         ("empty", False, "Insurance"))
        self.assertNotIn("selected_by", missing)

    def test_only_public_fec_filings_can_be_linked(self):
        self.assertEqual(_source_url("https://docquery.fec.gov/pdf/123/filing.pdf#page=3"),
                         "https://docquery.fec.gov/pdf/123/filing.pdf")
        self.assertEqual(_source_url("https://docquery.fec.gov/cgi-bin/fecimg/?202004249223569349"),
                         "https://docquery.fec.gov/cgi-bin/fecimg/?202004249223569349")
        for url in ("javascript:alert(1)", "http://docquery.fec.gov/filing.pdf", "https://evil.test/file",
                    "https://docquery.fec.gov.evil.test/file", "https://name:secret@www.fec.gov/file",
                    "https://docquery.fec.gov/file?api_key=secret", "https://www.fec.gov/file?api%5Fkey=secret",
                    "https://www.fec.gov:9000/file", "https://api.open.fec.gov/v1/?api_key=secret"):
            self.assertEqual(_source_url(url), "", url)

    def test_http_endpoint_is_read_only_and_rejects_client_paths(self):
        source = Path(__file__).resolve().parents[1] / "app" / "server.py"
        spec = importlib.util.spec_from_file_location("cross_reference_http_test", source)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        server.app = SimpleNamespace(read_only=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(app, "latest_cross_reference", return_value={"available": False, "status": "empty"}) as read:
                with urlopen(base + "/api/cross-reference") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["status"], "empty")
                read.assert_called_once_with(app.PROJECT / "state" / "cross-reference")
                for query in ("?path=C:/private", "?run=../../outside", "?unknown"):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "/api/cross-reference" + query)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                self.assertEqual(read.call_count, 1)
            for asset in ("cross-reference.js", "cross-reference.css"):
                with urlopen(base + "/" + asset) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_offline_cli_result_reaches_the_dashboard_as_a_digest_and_no_total(self):
        database = Path(self.temp.name) / "fixture.db"
        columns = """sub_id TEXT PRIMARY KEY,company TEXT,industry TEXT,
            two_year_transaction_period INTEGER,contribution_receipt_amount REAL,
            contributor_name TEXT,contributor_zip TEXT,contribution_receipt_date TEXT,
            committee_id TEXT,transaction_id TEXT,file_num INTEGER,filing_form TEXT,line_number TEXT,
            entity_type TEXT,company_match_status TEXT,contributor_id TEXT,memo_code TEXT,
            back_reference_transaction_id TEXT"""
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE contributions(" + columns + ")")
            db.execute("CREATE TABLE corporate_giving(" + columns.replace("two_year_transaction_period", "cycle") + ")")
            db.execute("CREATE TABLE companies(company TEXT,industry TEXT,variant TEXT)")
            db.execute("INSERT INTO companies VALUES('A Bank','UI Fixture','A Bank')")
            def record(sid, amount, name="PERSON, ONE", entity="IND", donor_id=None, memo=None, reference=None):
                return (sid, "A Bank", "UI Fixture", 2024, amount, name, "12345", "2024-01-01",
                        "C00000001", sid, 10, "F3X", "11AI", entity, "accepted", donor_id, memo, reference)
            # Large is relative to the run, so the individual class needs enough
            # ordinary receipts to have a distribution the one outlier stands out of.
            db.executemany("INSERT INTO contributions VALUES(" + ",".join("?" for _ in range(18)) + ")",
                           [record("a", 100), record("memo", 100, memo="X", reference="a"),
                            record("big", 250_000),
                            *(record(f"small{index:02d}", 1) for index in range(20))])
            db.execute("INSERT INTO corporate_giving VALUES(" + ",".join("?" for _ in range(18)) + ")",
                       record("pac", 60, name="A BANK PAC", entity="PAC", donor_id="C00000002"))
        original = database.read_bytes()
        runner = Path(__file__).resolve().parents[1] / "tools" / "run_cross_reference.py"
        result = subprocess.run([sys.executable, str(runner), "--database", str(database), "--industry", "UI Fixture",
                                 "--cycles", "2024", "--state-root", str(self.root),
                                 "--baseline-root", str(self.root / "no-baseline"), "--offline"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshot = latest_cross_reference(self.root)
        self.assertTrue(snapshot["available"], snapshot)
        self.assertEqual(snapshot["status"], "completed")
        report = snapshot["report"]
        self.assertTrue(report["digest_available"])
        digest = report["digest"]
        # The runner wrote the digest beside its report, and it is the same file.
        saved = json.loads((self.root / snapshot["run_id"] / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["run_id"], snapshot["run_id"])
        self.assertEqual(saved["counts"]["source_records"], 24)
        self.assertEqual(digest["outliers"]["large_count"], 1)
        self.assertEqual(digest["outliers"]["large"][0]["record_id"], "big")
        self.assertEqual(digest["outliers"]["large"][0]["amount"], 250_000.0)
        self.assertEqual(digest["outliers"]["large"][0]["entity"], "IND")
        self.assertIn("proposed default", digest["basis"])
        self.assertNotIn("summary", report)
        self.assertNotIn("observed_original_giving", json.dumps(snapshot))
        self.assertEqual(database.read_bytes(), original)
        self.assertNotIn("PERSON, ONE", json.dumps(snapshot))
        self.assertTrue((self.root / snapshot["run_id"] / "ledger.sqlite").exists())

    def test_collection_child_uses_central_evidence_baseline_and_budget_roots(self):
        source = Path(__file__).resolve().parents[1] / "app" / "server.py"
        spec = importlib.util.spec_from_file_location("cross_reference_launch_test", source)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        original_read = app.read_json
        # This checks the pinned state roots the child inherits, not the FEC bulk
        # staging, and those snapshots are not kept in git.
        with patch.object(app, "environment_keys", return_value=[]), \
             patch.object(app, "load_keys", return_value=["offline-fixture"]), \
             patch.object(app, "copy_fec_sources"), \
             patch.object(app, "read_json", side_effect=lambda path: {} if Path(path).name == "active_fec_sources.json" else original_read(path)):
            console = app.Console(Path(self.temp.name) / "console", rscript=sys.executable)
            roster = console.create_roster({"sector": "UI Fixture", "companies": [{"name": "A Bank", "aliases": ["AB"]}]})
            process = MagicMock(pid=987654)
            process.poll.return_value = None
            with patch.object(app.subprocess, "Popen", return_value=process) as launch, patch.object(app.threading, "Thread"):
                console.launch({"id": roster["id"], "cycles": [2024]})
            environment = launch.call_args.kwargs["env"]
            for name, relative in (("MENTAT_CROSS_REFERENCE_ROOT", "state/cross-reference"),
                                   ("MENTAT_ACCEPTED_SOURCE_ROOT", "state/accepted-report-sources"),
                                   ("MENTAT_API_BUDGET_DB", ".cache/fec-api-budget.sqlite")):
                self.assertEqual(Path(environment[name]), app.PROJECT / relative)
                self.assertNotIn("runtime", environment[name])
            expected_library = app.r_library_path(environment, app.PROJECT, console.rscript)
            if expected_library:
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index('-RLibraryPath') + 1], expected_library)

    def test_r_library_prefers_existing_user_configuration_then_verified_workspace(self):
        source = Path(__file__).resolve().parents[1] / "app" / "server.py"
        spec = importlib.util.spec_from_file_location("cross_reference_r_library_test", source)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        local = Path(self.temp.name) / 'local'
        project = Path(self.temp.name) / 'project'
        workspace = project / '.cache' / 'audit-r-library'
        explicit = Path(self.temp.name) / 'explicit'
        explicit.mkdir()
        for package in ('digest', 'DBI', 'RSQLite', 'data.table'):
            directory = workspace / package
            directory.mkdir(parents=True)
            (directory / 'DESCRIPTION').write_text('Package: ' + package)
        environment = {'LOCALAPPDATA': str(local), 'R_LIBS_USER': str(explicit)}
        self.assertEqual(Path(app.r_library_path(environment, project, 'C:/R/R-4.6.0/bin/Rscript.exe')), explicit)
        environment['R_LIBS_USER'] = str(local / 'missing')
        self.assertEqual(Path(app.r_library_path(environment, project, 'C:/R/R-4.6.0/bin/Rscript.exe')), workspace)
        user_library = local / 'R' / 'win-library' / '4.6'
        user_library.mkdir(parents=True)
        self.assertEqual(Path(app.r_library_path(environment, project, 'C:/R/R-4.6.0/bin/Rscript.exe')), user_library)
        self.assertEqual(environment['R_LIBS_USER'], str(local / 'missing'))


if __name__ == "__main__":
    unittest.main()
