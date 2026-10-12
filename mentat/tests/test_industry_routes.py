"""The industry-scoped routes against contracts/contributions_api.json.

Every request in the contract is issued exactly as written against a real
in-process server on port 0, and the contract file is read from disk -- no
example payload is copied into this file.

Scope of what is proved here, stated plainly:

* The registry, the resolution rule and everything server.py adds (the
  `industry` echo, `build_stamp`, the dataset filter, the two 400s, the body
  rewriting before a reader is called) run for real.
* `contribution_data._sources` is replaced by the same synthetic snapshots the
  registry suite uses, because the real reader needs a published workbook with
  a pinned payload. `/api/contributions/catalog` and `/api/snapshots` are
  otherwise unpatched: the real catalog producer runs over those sources.
* `contribution_query.query`/`records` and `contribution_geo.geography` are
  doubled, because a real one needs an indexed database. The double returns the
  contract's own response minus the two keys the route is supposed to add, so
  what is asserted about those routes is exactly the route's contribution --
  and the double records the body it was handed, which is how the dataset
  back-fill and the industry strip are proved.
"""
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

APP = Path(__file__).resolve().parents[1] / "app"
CONTRACTS = Path(__file__).resolve().parents[2] / "audits" / "mentat_failings_20260915" / "contracts"


def load_contract(name):
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


spec = importlib.util.spec_from_file_location("industry_routes_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CONTRACT = load_contract("contributions_api")
EXAMPLES = CONTRACT["examples"]
BY_NAME = {example["name"]: example for example in EXAMPLES}
EXACT = ("industry", "build_stamp", "dataset", "error")
INDUSTRIES = ("Banking", "Credit Union")
# Bound before any test doubles it: the example-13 acceptance run needs the real
# folder scan, not the synthetic snapshots every other test here is served.
REAL_SOURCES = app.contribution_data._sources


def synthetic(industry, field):
    return hashlib.sha256(f"example:{industry}:{field}".encode()).hexdigest()


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def sources(directory, industries=INDUSTRIES):
    """Synthetic contribution_data._sources() items carrying contract hashes."""
    result = []
    for industry in industries:
        workbook = directory / (industry + " Industry Spending Overview.xlsx")
        if not workbook.is_file():
            workbook.write_bytes(b"overview " + industry.encode())
        cache = (".cache/receipt-metadata-repair-20260910/credit_union" if industry == "Credit Union"
                 else ".cache/industry-report/" + industry.lower().replace(" ", "_"))
        item = {"id": industry, "industry": industry, "label": industry,
                "workbook": workbook, "workbook_sha256": synthetic(industry, "workbook"),
                "available": True,
                "database": app.snapshot_registry.PROJECT / cache / "mentat.db",
                "database_sha256": synthetic(industry, "database"),
                "original_database_sha256": synthetic(industry, "database"),
                "payload_sha256": synthetic(industry, "payload"),
                "destination_schema": 1,
                "hydration_manifest": (app.snapshot_registry.PROJECT / cache / "mentat.db.hydration.json"
                                       if industry == "Credit Union" else None)}
        if industry == "Credit Union":
            companion = directory / (industry + " Industry Spending Data.xlsx")
            if not companion.is_file():
                companion.write_bytes(b"companion " + industry.encode())
            item.update(data_workbook=companion, data_workbook_sha256=synthetic(industry, "companion"),
                        data_lineage_matches=True)
        result.append(item)
    return result


class RouteBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.reports = Path(cls.temp.name) / "reports"
        cls.reports.mkdir()
        cls.snapshots = sources(cls.reports)
        cls.console = SimpleNamespace(token="industry-route-token", read_only=False,
                                      report_directory=cls.reports,
                                      configured_root=str(cls.reports), root=cls.reports)
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.server.app = cls.console
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.scans = []

        def read_sources(directory):
            self.scans.append(directory)
            return list(self.snapshots)

        for target, attribute, value in (
                (app.contribution_data, "_sources", read_sources),
                (app.contribution_workbook_guard, "verify_sources", lambda *_a, **_k: [])):
            fake = patch.object(target, attribute, value)
            fake.start()
            self.addCleanup(fake.stop)

    def request(self, path, *, method="GET", data=None, timeout=10):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=timeout)
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Host": "127.0.0.1:{}".format(self.server.server_port)}
        if method == "POST":
            headers.update({"X-Mentat-Token": self.console.token, "Content-Type": "application/json"})
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            parsed = (json.loads(content) if response.getheader("Content-Type") == "application/json"
                      else content)
            return response.status, parsed
        finally:
            connection.close()

    def url(self, example):
        path = example["path"]
        return path + "?" + urlencode(example["query"]) if example.get("query") else path


class IndustryRouteTests(RouteBase):
    """Contract examples 1-12 and 15-16."""

    def setUp(self):
        super().setUp()
        self.calls = []

        def reader(directory, request, sources=None):
            self.calls.append((directory, json.loads(json.dumps(request))))
            return json.loads(json.dumps(self.payload))

        def geography(request):
            self.calls.append((None, json.loads(json.dumps(request))))
            return json.loads(json.dumps(self.payload))

        self.payload = {}
        for target, attribute, value in ((app.contribution_query, "query", reader),
                                         (app.contribution_query, "records", reader),
                                         (app.contribution_geo, "geography", geography)):
            fake = patch.object(target, attribute, value)
            fake.start()
            self.addCleanup(fake.stop)

    def send(self, example):
        """The double answers with the contract's own response, minus what the route adds."""
        self.payload = {key: value for key, value in example["response"].items()
                        if key not in ("industry", "build_stamp")}
        return self.request(self.url(example), method=example["method"], data=example.get("body"))

    def compare(self, expected, actual, trail=""):
        """Every example key exists with the same JSON type; the four named keys match exactly."""
        self.assertEqual(json_type(expected), json_type(actual), "type at " + (trail or "<root>"))
        if isinstance(expected, dict):
            for key, value in expected.items():
                if key == "...":
                    continue
                self.assertIn(key, actual, "missing key at " + trail + "/" + key)
                if key in EXACT:
                    self.assertEqual(value, actual[key], "value at " + trail + "/" + key)
                self.compare(value, actual[key], trail + "/" + key)
        elif isinstance(expected, list) and expected:
            self.assertTrue(actual, "empty list at " + trail)
            self.compare(expected[0], actual[0], trail + "[0]")

    def check(self, index):
        example = EXAMPLES[index - 1]
        status, body = self.send(example)
        self.assertEqual(status, example["status"], (example["name"], body))
        self.compare(example["response"], body)
        return body

    def test_examples_1_to_3_session_carries_the_scope_and_the_whole_registry(self):
        for index in (1, 2, 3):
            example = EXAMPLES[index - 1]
            status, body = self.send(example)
            self.assertEqual((index, status), (index, 200), body)
            self.compare(example["response"], body)
            self.assertEqual(body["industries"], example["response"]["industries"])
        # Example 3 addresses the same snapshot by slug and gets the label back.
        self.assertEqual(EXAMPLES[2]["query"]["industry"], "credit_union")
        self.assertEqual(self.send(EXAMPLES[2])[1]["industry"], "Credit Union")

    def test_example_4_catalog_without_a_parameter_lists_every_snapshot(self):
        body = self.check(4)
        self.assertEqual([item["id"] for item in body["datasets"]], list(INDUSTRIES))
        self.assertIsNone(body["industry"])
        for item in body["datasets"]:
            self.assertEqual(item["build_stamp"], app.snapshot_registry.build_stamp(
                synthetic(item["id"], "workbook"),
                synthetic(item["id"], "companion") if item["id"] == "Credit Union" else None,
                synthetic(item["id"], "database"), synthetic(item["id"], "payload")))

    def test_example_5_catalog_with_an_industry_keeps_only_that_dataset(self):
        body = self.check(5)
        self.assertEqual([item["id"] for item in body["datasets"]], ["Banking"])
        # A slug selects the same one dataset.
        status, slugged = self.request("/api/contributions/catalog?industry=credit_union")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in slugged["datasets"]], ["Credit Union"])
        self.assertEqual(slugged["industry"], "Credit Union")

    def test_example_6_geography_catalog_accepts_and_echoes_the_industry_only(self):
        body = self.check(6)
        self.assertEqual(body["industry"], "Banking")
        # geo_catalog() is industry-independent: the payload must not change.
        status, plain = self.request("/api/contributions/geography/catalog")
        self.assertIsNone(plain["industry"])
        self.assertEqual({k: v for k, v in plain.items() if k != "industry"},
                         {k: v for k, v in body.items() if k != "industry"})

    def test_examples_7_to_9_query_resolves_industry_dataset_or_both(self):
        for index in (7, 8, 9):
            self.calls = []
            example = EXAMPLES[index - 1]
            status, body = self.send(example)
            self.assertEqual((index, status), (index, 200), body)
            self.compare(example["response"], body)
            self.assertEqual(body["industry"], "Banking")
            self.assertEqual(body["build_stamp"], app.snapshot_registry.build_stamp(
                synthetic("Banking", "workbook"), None,
                synthetic("Banking", "database"), synthetic("Banking", "payload")))
            # The reader selects by dataset and never sees `industry`.
            self.assertEqual(len(self.calls), 1)
            directory, seen = self.calls[0]
            self.assertEqual(directory, self.reports)
            self.assertNotIn("industry", seen)
            self.assertEqual(seen["dataset"], "Banking")

    def test_example_10_records_answers_with_the_same_scope_keys(self):
        body = self.check(10)
        self.assertEqual((body["industry"], body["dataset"]), ("Banking", "Banking"))
        self.assertNotIn("industry", self.calls[0][1])

    def test_example_11_geography_echoes_the_scope_and_strips_it_from_the_body(self):
        body = self.check(11)
        self.assertEqual(body["industry"], "Banking")
        seen = self.calls[0][1]
        self.assertNotIn("industry", seen)
        self.assertEqual(seen["zips"], EXAMPLES[10]["body"]["zips"])

    def test_example_12_snapshots_filters_to_one_entry(self):
        body = self.check(12)
        self.assertEqual([item["industry"] for item in body["snapshots"]], ["Banking"])
        status, every = self.request("/api/snapshots")
        self.assertEqual(status, 200)
        self.assertEqual([item["industry"] for item in every["snapshots"]], list(INDUSTRIES))
        self.assertEqual(status, 200)
        status, unknown = self.request("/api/snapshots?industry=Fintec")
        self.assertEqual(status, 400)
        self.assertEqual(unknown, {"error": "Choose a registered industry: Banking, Credit Union."})

    def test_example_15_an_unknown_industry_is_the_one_error_key_the_server_emits(self):
        body = self.check(15)
        self.assertEqual(list(body), ["error"])

    def test_example_16_industry_and_dataset_that_disagree_are_refused(self):
        body = self.check(16)
        self.assertEqual(body, {"error": "The industry and dataset in this request disagree."})
        # Nothing was read: the refusal happens before the reader is called.
        self.assertEqual(self.calls, [])
        for route in ("/api/contributions/query", "/api/contributions/records",
                      "/api/contributions/geography"):
            with self.subTest(route=route):
                status, refused = self.request(route, method="POST",
                                               data={"industry": "Banking", "dataset": "Credit Union"})
                self.assertEqual((status, refused),
                                 (400, {"error": "The industry and dataset in this request disagree."}))
        self.assertEqual(self.calls, [])

    def test_every_contract_negative_control_is_refused_with_an_error_string(self):
        controls = CONTRACT["negative_controls"]
        self.assertTrue(controls, "a contract with no negative control is not finished")
        for control in controls:
            with self.subTest(why=control["why"][:60]):
                self.calls = []
                status, body = self.request("/api/contributions/query", method="POST",
                                            data=control["payload"])
                self.assertEqual(status, 400, body)
                self.assertIsInstance(body.get("error"), str)
                self.assertTrue(body["error"])
                self.assertEqual(list(body), ["error"])
                self.assertEqual(self.calls, [])

    def test_the_negative_control_assertion_has_teeth(self):
        # The same route, with a registered industry, answers 200.
        self.payload = {"status": "ready", "rows": []}
        status, body = self.request("/api/contributions/query", method="POST",
                                    data={"industry": "Banking"})
        self.assertEqual((status, body["industry"]), (200, "Banking"))

    def test_a_request_reads_the_report_folder_at_most_once(self):
        """The registry is not free: every snapshot's manifest and payload header
        is read to build it. A route must not scan the folder twice, and a route
        with no industry and no dataset on it must not scan at all."""
        self.payload = {"status": "ready", "rows": []}
        for label, issue in (
                ("catalog", lambda: self.request("/api/contributions/catalog")),
                ("catalog ?industry=", lambda: self.request("/api/contributions/catalog?industry=Banking")),
                ("session", lambda: self.request("/api/contributions/session")),
                ("snapshots", lambda: self.request("/api/snapshots")),
                ("query industry", lambda: self.request("/api/contributions/query", method="POST",
                                                        data={"industry": "Banking"}))):
            with self.subTest(route=label):
                self.scans = []
                issue()
                self.assertLessEqual(len(self.scans), 1, "%s scanned %d times" % (label, len(self.scans)))
        # Nothing to resolve means nothing is read.
        for label, issue in (
                ("geography/catalog", lambda: self.request("/api/contributions/geography/catalog")),
                ("geography body", lambda: self.request("/api/contributions/geography", method="POST",
                                                        data={"zips": ["20001"]}))):
            with self.subTest(route=label, scope="none"):
                self.scans = []
                issue()
                self.assertEqual(self.scans, [], "%s scanned the folder for nothing" % label)
        # The graph and comparison routes go through label_or_value, which the
        # fixture console cannot serve end to end (it is a namespace, not a
        # Console), so the same claim is made against the resolver directly.
        self.scans = []
        self.assertEqual(app.industry_scope.label_or_value(self.reports, ""), "")
        self.assertEqual(self.scans, [], "an empty graph industry scanned the folder")
        self.assertEqual(app.industry_scope.label_or_value(self.reports, "banking"), "Banking")
        self.assertEqual(len(self.scans), 1)

    def test_a_blank_industry_on_a_get_is_refused_not_silently_ignored(self):
        """parse_qs drops a blank value, which read `?industry=` as no industry.

        The request said industry and named none, so the contract's rule 1
        applies and the answer is the registry refusal - not the legacy
        every-snapshot view with `industry: null`, which is what a client
        binding an empty picker to the query string would have got.
        """
        refusal = {"error": "Choose a registered industry: Banking, Credit Union."}
        for path in ("/api/contributions/catalog?industry=",
                     "/api/contributions/session?industry=",
                     "/api/snapshots?industry=",
                     "/api/contributions/geography/catalog?industry="):
            with self.subTest(path=path):
                status, body = self.request(path)
                self.assertEqual((status, body), (400, refusal))
        # Teeth: the same routes with no industry key at all keep the legacy view.
        for path in ("/api/contributions/catalog", "/api/contributions/session",
                     "/api/contributions/geography/catalog"):
            with self.subTest(path=path, scope="absent"):
                status, body = self.request(path)
                self.assertEqual(status, 200, body)
                self.assertIsNone(body["industry"])

    def test_the_shape_comparison_has_teeth(self):
        # compare() is the assertion every example test rests on. Prove it fails.
        response = EXAMPLES[6]["response"]
        for label, broken in (("dropped key", {k: v for k, v in response.items() if k != "build_stamp"}),
                              ("wrong industry", {**response, "industry": "Credit Union"}),
                              ("emptied list", {**response, "rows": []}),
                              ("wrong type", {**response, "group_count": "1"})):
            with self.subTest(broken=label):
                with self.assertRaises(AssertionError):
                    self.compare(response, broken)

    def test_a_slug_addresses_every_industry_scoped_route(self):
        self.payload = {"status": "ready", "rows": []}
        status, body = self.request("/api/contributions/session?industry=credit_union")
        self.assertEqual((status, body["industry"]), (200, "Credit Union"))
        status, body = self.request("/api/contributions/query", method="POST",
                                    data={"industry": "credit_union"})
        self.assertEqual((status, body["industry"]), (200, "Credit Union"))
        self.assertEqual(self.calls[-1][1]["dataset"], "Credit Union")
        status, body = self.request("/api/snapshots?industry=credit_union")
        self.assertEqual([item["industry"] for item in body["snapshots"]], ["Credit Union"])


class GraphAndCrossReferenceTests(RouteBase):
    """Contract examples 13 and 14."""

    def state(self, root, runs, pointer=None):
        base = root / "state" / "cross-reference"
        base.mkdir(parents=True, exist_ok=True)
        written = {}
        for run_id, industry in runs:
            directory = base / run_id
            directory.mkdir()
            report = {"schema_version": 1, "run_id": run_id, "status": "completed",
                      "generated_at": "2026-09-16T12:00:00+00:00",
                      "scope": {"industries": [industry], "companies": ["Example Corp"],
                                "cycles": ["2025-2026"], "source_count": 1},
                      "summary": {"input_records": 10}, "partisan_breakdowns": [],
                      "steps": [{"id": step, "status": "completed", "detail": ""}
                                for step in ("define", "collect", "identify", "connect", "count", "verify")],
                      "unresolved_reasons": [], "coverage_reasons": [], "review_queue": [],
                      "changes": [], "limitations": []}
            raw = json.dumps(report).encode("utf-8")
            (directory / "report.json").write_bytes(raw)
            (directory / "progress.json").write_text(json.dumps(
                {"schema_version": 1, "run_id": run_id, "status": "completed",
                 "updated_at": "2026-09-16T12:00:00+00:00", "message": "",
                 "steps": report["steps"]}), encoding="utf-8")
            written[run_id] = hashlib.sha256(raw).hexdigest()
        if pointer:
            (base / "latest.json").write_text(json.dumps(
                {"schema_version": 1, "run_id": pointer, "report_sha256": written[pointer]}),
                encoding="utf-8")
        return written

    def acceptance_subject(self):
        """The smallest published overview both /api/graph and the registry own.

        The folder is the one the application resolves -- MENTAT_REPORT_DIRECTORY,
        else config/report-directory.json -- and it is never created here. The
        console is read-only and every workbook below is opened for reading only.
        """
        absent = ("no published '<Industry> Industry Spending Overview.xlsx' that /api/graph can serve "
                  "is readable in the authoritative report directory; the manual acceptance receipt is "
                  "audits/mentat_failings_20260915/receipts/S4_acceptance_real_snapshots.txt")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        candidates = []
        try:
            console = app.Console(Path(temporary.name), rscript=sys.executable, read_only=True)
            rows = {row["index"]: row for row in console.published_reports()
                    if row["kind"] == "overview"
                    and row["name"].casefold().endswith(" industry spending overview.xlsx")}
            registry = app.snapshot_registry.registry(console.report_directory)
            for group in console.graph_catalog("published-reports", list(rows.values()))["industries"]:
                entry = app.snapshot_registry.find(registry, group["name"])
                books = [console.report_directory / rows[index]["name"]
                         for index in group["indices"] if index in rows]
                # One workbook per group keeps `reports` a single stamped entry.
                if entry and len(books) == 1 and books[0].is_file():
                    candidates.append((books[0].stat().st_size, group["name"], books[0], entry))
        except (OSError, ValueError) as error:
            self.skipTest("%s (%s)" % (absent, error))
        if not candidates:
            self.skipTest(absent)
        _size, industry, workbook, entry = min(candidates, key=lambda item: item[0])
        return console, industry, workbook, entry

    def test_example_13_graph_is_an_acceptance_run(self):
        """The one example no double can answer: /api/graph reads a real workbook.

        It runs wherever the authoritative folder holds one the route can serve,
        and skips -- saying what is missing and where the manual receipt is --
        where it does not, which is any clean checkout.
        """
        example = EXAMPLES[12]
        self.assertEqual(example["path"], "/api/graph")
        real = patch.object(app.contribution_data, "_sources", REAL_SOURCES)
        real.start()
        self.addCleanup(real.stop)
        console, industry, workbook, entry = self.acceptance_subject()
        before = workbook.stat()
        # This console in place of the namespace the doubled routes are served by:
        # /api/graph reads the folder, the workbook and the registry for itself.
        with patch.object(self.server, "app", console):
            status, body = self.request(
                example["path"] + "?" + urlencode(dict(example["query"], industry=industry)), timeout=60)
            self.assertEqual(status, example["status"], body)
            # A slug addresses the same run and answers with the label.
            slug_status, slugged = self.request(
                example["path"] + "?" + urlencode(dict(example["query"], industry=entry["slug"])), timeout=60)
        self.compare_keys({key: value for key, value in example["response"].items() if key != "..."}, body)
        self.assertEqual(body["industry"], industry)
        self.assertEqual([report["name"] for report in body["reports"]], [workbook.name])
        self.assertEqual(body["reports"][0]["build_stamp"], entry["build_stamp"])
        self.assertEqual((slug_status, slugged["industry"]), (200, industry))
        after = workbook.stat()
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns),
                         "the acceptance run wrote to " + str(workbook))

    def test_example_14_industry_selects_its_own_run_and_the_pointer_still_serves_latest(self):
        example = EXAMPLES[13]
        self.assertEqual(example["path"], "/api/cross-reference")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            digests = self.state(root, [("20260916-000001", "Credit Union"),
                                        ("20260916-000002", "Banking")],
                                 pointer="20260916-000001")
            with patch.object(app, "PROJECT", root):
                status, latest = self.request("/api/cross-reference")
                self.assertEqual(status, 200, latest)
                self.assertEqual(latest["run_id"], "20260916-000001")
                self.assertEqual(latest["selected_by"], "pointer")
                self.assertEqual(latest["industry"], "Credit Union")

                status, banking = self.request("/api/cross-reference?industry=Banking")
                self.assertEqual(status, 200, banking)
                self.assertEqual(banking["run_id"], "20260916-000002")
                self.assertEqual(banking["selected_by"], "industry")
                self.assertEqual(banking["industry"], "Banking")
                self.assertEqual(banking["report_sha256"], digests["20260916-000002"])
                self.assertEqual(banking["report"]["scope"]["industries"], ["Banking"])

                # A slug addresses the same view, and it is not the pointer's run.
                status, union = self.request("/api/cross-reference?industry=credit_union")
                self.assertEqual((status, union["run_id"]), (200, "20260916-000001"))
                self.assertEqual(union["selected_by"], "industry")
                self.compare_keys(example["response"], banking)

    def compare_keys(self, expected, actual):
        for key, value in expected.items():
            self.assertIn(key, actual)
            self.assertEqual(json_type(value), json_type(actual[key]), key)

    def test_the_view_still_refuses_every_other_query_key(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.state(root, [("20260916-000002", "Banking")], pointer="20260916-000002")
            refusal = "The cross-reference view accepts no path or run parameters."
            with patch.object(app, "PROJECT", root):
                for query in ("?run=20260916-000002", "?path=C:/x", "?industry=Banking&run=x", "?id=1"):
                    with self.subTest(query=query):
                        status, body = self.request("/api/cross-reference" + query)
                        self.assertEqual((status, body), (400, {"error": refusal}))
                status, body = self.request("/api/cross-reference?industry=Fintec")
                self.assertEqual((status, body),
                                 (400, {"error": "Choose a registered industry: Banking, Credit Union."}))

    def test_an_industry_with_no_saved_run_says_so_without_serving_another_run(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.state(root, [("20260916-000001", "Credit Union")], pointer="20260916-000001")
            with patch.object(app, "PROJECT", root):
                status, body = self.request("/api/cross-reference?industry=Banking")
                self.assertEqual(status, 200, body)
                self.assertEqual(body, {"available": False, "status": "empty", "industry": "Banking",
                                        "message": "No cross-reference run has been saved for this industry yet."})


if __name__ == "__main__":
    unittest.main()
