"""Saved views and briefing items against contracts/studio_api.json.

Same harness as test_studio_views.py: a real Console on a temporary data root,
so a saved view is a real file another console on that root could list. The
contract is read from disk; no example payload is copied into this file.

The server's half of the DE-021 fix is what runs here: the save gate that
refuses an industry of the wrong shape or in the wrong place, the list that
reports which industries a view addresses, and the byte-intact round trip of
`source` and `sheets`. The client producers (viewSourceRecord, createSheet, the
load-time migration) are asserted by the JavaScript suite, not this one.
"""
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
CONTRACTS = Path(__file__).resolve().parents[2] / "audits" / "mentat_failings_20260915" / "contracts"


def load_contract(name):
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


spec = importlib.util.spec_from_file_location("studio_industry_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CONTRACT = load_contract("studio_api")
EXAMPLES = CONTRACT["examples"]


class StudioIndustryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data_root = Path(cls.temp.name) / "data"
        cls.report_root = Path(cls.temp.name) / "reports"
        cls.data_root.mkdir()
        cls.report_root.mkdir()
        with patch.object(app, "report_directory", return_value=cls.report_root):
            cls.console = app.Console(str(cls.data_root), read_only=True)
        cls.console.read_only = False
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.server.app = cls.console
        cls.console.port = cls.server.server_port
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.console.read_only = False
        self.views = self.console.root / "studio" / "views"
        if self.views.is_dir():
            for path in self.views.glob("*.json"):
                path.unlink()
        briefing = self.console.root / "studio" / "briefing"
        if briefing.is_dir():
            for path in briefing.iterdir():
                path.unlink()

    def request(self, path, *, method="GET", data=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
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

    def save(self):
        status, body = self.request("/api/studio/views", method="POST", data=EXAMPLES["views_save"])
        self.assertEqual(status, 200, body)
        self.assertRegex(body["id"], "^[a-f0-9]{32}$")
        self.assertGreater(body["saved_at"], 0)
        return body["id"]

    def test_the_two_industry_example_saves_and_reloads_byte_intact(self):
        identifier = self.save()
        status, one = self.request("/api/studio/views?id=" + identifier)
        self.assertEqual(status, 200, one)
        view = one["view"]
        self.assertEqual(view["source"], EXAMPLES["views_save"]["source"])
        self.assertEqual(view["sheets"], EXAMPLES["views_save"]["sheets"])
        self.assertEqual(view["sourceKey"], EXAMPLES["views_save"]["sourceKey"])
        self.assertEqual(view["active"], EXAMPLES["views_save"]["active"])
        # The file on disk carries the same two values, not a re-derived copy.
        stored = json.loads((self.views / (identifier + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(stored["source"], EXAMPLES["views_save"]["source"])
        self.assertEqual(stored["sheets"], EXAMPLES["views_save"]["sheets"])
        # The fan-out list survives with every entry's industry.
        self.assertEqual([item["industry"] for item in view["sheets"][1]["sources"]],
                         ["Credit Union", "Banking"])
        # A save response must match the contract's shape.
        for key in EXAMPLES["views_save_response"]:
            self.assertIn(key, {"id": identifier, "saved_at": stored["saved_at"]})

    def test_the_list_reports_which_industries_a_view_addresses(self):
        self.save()
        status, listed = self.request("/api/studio/views")
        self.assertEqual(status, 200, listed)
        self.assertEqual(len(listed["views"]), 1)
        entry = listed["views"][0]
        self.assertEqual(entry["industries"], EXAMPLES["views_list_item"]["industries"])
        self.assertEqual(entry["industries"], ["Banking", "Credit Union"])
        for key, value in EXAMPLES["views_list_item"].items():
            with self.subTest(key=key):
                self.assertIn(key, entry)
                self.assertEqual(type(entry[key]), type(value), key)
        self.assertEqual(entry["sheet_count"], EXAMPLES["views_list_item"]["sheet_count"])
        self.assertNotIn("sheets", entry)

    def test_industries_is_distinct_and_in_first_seen_order(self):
        payload = json.loads(json.dumps(EXAMPLES["views_save"]))
        payload["source"]["industry"] = "Credit Union"
        payload["sheets"][0]["source"]["industry"] = "Telecom"
        status, body = self.request("/api/studio/views", method="POST", data=payload)
        self.assertEqual(status, 200, body)
        _, listed = self.request("/api/studio/views")
        entry = next(view for view in listed["views"] if view["id"] == body["id"])
        self.assertEqual(entry["industries"], ["Credit Union", "Telecom", "Banking"])

    def test_a_view_with_no_industry_anywhere_still_saves_and_lists_empty(self):
        # A saved view from before this change must keep working unchanged.
        payload = {"name": "Legacy", "sourceKey": "Banking|2025-2026", "source": {"dataset": "Banking"},
                   "sheets": [{"name": "Recipients"}], "active": 0}
        status, body = self.request("/api/studio/views", method="POST", data=payload)
        self.assertEqual(status, 200, body)
        _, listed = self.request("/api/studio/views")
        self.assertEqual(listed["views"][0]["industries"], [])

    def test_membership_in_the_registry_is_deliberately_not_checked(self):
        # A view must reload after its snapshot is retired; the client reports
        # an unaddressable source at open time, the server does not refuse it.
        payload = json.loads(json.dumps(EXAMPLES["views_save"]))
        payload["source"]["industry"] = "A Retired Industry"
        status, body = self.request("/api/studio/views", method="POST", data=payload)
        self.assertEqual(status, 200, body)

    def test_the_briefing_item_keeps_its_industry_verbatim(self):
        status, body = self.request("/api/studio/briefing/add", method="POST",
                                    data=EXAMPLES["briefing_add"])
        self.assertEqual(status, 200, body)
        status, tray = self.request("/api/studio/briefing")
        self.assertEqual(status, 200, tray)
        item = next(entry for entry in tray["items"] if entry["id"] == body["id"])
        self.assertEqual(item["source"], EXAMPLES["briefing_add"]["source"])
        self.assertEqual(item["source"]["industry"], "Banking")
        for key, value in EXAMPLES["briefing_item"].items():
            with self.subTest(key=key):
                self.assertIn(key, item)
                self.assertEqual(type(item[key]), type(value), key)

    def wrap(self, payload):
        """A negative control is a source, a sources list, or a whole save body."""
        if isinstance(payload, dict) and "sheets" in payload:
            return payload
        body = json.loads(json.dumps(EXAMPLES["views_save"]))
        if isinstance(payload, dict):
            body["source"] = payload
        else:
            body["sheets"][1]["sources"] = payload
        return body

    def test_every_contract_negative_control_is_refused(self):
        controls = CONTRACT["negative_controls"]
        self.assertTrue(controls, "a contract with no negative control is not finished")
        for control in controls:
            with self.subTest(why=control["why"][:70]):
                status, body = self.request("/api/studio/views", method="POST",
                                            data=self.wrap(control["payload"]))
                self.assertEqual(status, 400, body)
                self.assertIsInstance(body.get("error"), str)
                self.assertEqual(list(body), ["error"])
        # Nothing a refusal touched reached disk.
        self.assertEqual(self.request("/api/studio/views")[1], {"views": []})

    def test_the_negative_control_assertion_has_teeth(self):
        # The same route, with the contract's own example, answers 200.
        self.assertEqual(self.request("/api/studio/views", method="POST",
                                      data=EXAMPLES["views_save"])[0], 200)

    def test_the_industry_belongs_inside_the_data_source(self):
        # save_view's record whitelist would silently drop a top-level industry,
        # which is the DE-021 failure shape. It is refused instead.
        payload = json.loads(json.dumps(EXAMPLES["views_save"]))
        payload["industry"] = "Banking"
        status, body = self.request("/api/studio/views", method="POST", data=payload)
        self.assertEqual((status, body),
                         (400, {"error": "A saved view carries its industry inside its data source."}))


if __name__ == "__main__":
    unittest.main()
