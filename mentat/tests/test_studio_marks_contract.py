"""studio_api.json v3 marks cards, sent to a real Console over HTTP (lane M).

The examples in the shared contract were produced by the browser's own writers
(chart-studio-core.js writeCards / serializeCard; app/tests/studio-marks-
contract.test.js asserts that). Here the same file is what the server is asked
to accept: a dual axis and a Shape card save and read back byte-intact, a v2
sheet is stored exactly as it always was, and every malformed card set is
refused with one sentence before anything reaches disk.

Same harness as test_studio_contract_examples.py: a temporary data root and an
empty report folder; nothing real is touched. Run from mentat/tests.
"""
import copy
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
STUDIO_API = json.loads((CONTRACTS / "studio_api.json").read_text(encoding="utf-8"))
EXAMPLES = STUDIO_API["examples"]
REFUSAL = "The saved view's marks cards are malformed."

spec = importlib.util.spec_from_file_location("studio_marks_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def v3_controls():
    # Lane P's map-layer controls (lane: 'P') are asserted by test_map_layers_server.py.
    return [control for control in STUDIO_API["negative_controls"]
            if control.get("lane") != "P"
            and isinstance(control["payload"], dict) and isinstance(control["payload"].get("sheets"), list)
            and any("marksCards" in sheet or "syncAxes" in sheet for sheet in control["payload"]["sheets"])]


class StudioMarksContractTests(unittest.TestCase):
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
        views = self.console.root / "studio" / "views"
        if views.is_dir():
            for path in views.glob("*.json"):
                path.unlink()

    def request(self, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Host": "127.0.0.1:{}".format(self.server.server_port)}
        body = None
        if payload is not None:
            headers.update({"X-Mentat-Token": self.console.token, "Content-Type": "application/json"})
            body = json.dumps(payload).encode()
        try:
            connection.request("POST" if payload is not None else "GET", path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            connection.close()

    def stored(self, identifier):
        return json.loads((self.console.root / "studio" / "views" / (identifier + ".json")).read_text(encoding="utf-8"))

    def test_the_contract_is_v3_and_carries_the_marks_examples_and_controls(self):
        self.assertEqual(STUDIO_API["version"], 3)
        for name in ("marks_card", "views_save_marks_dual", "views_get_marks_dual", "views_save_marks_shape",
                     "marks_v2_sheet", "marks_v2_cards", "marks_v2_written"):
            self.assertIn(name, EXAMPLES)
        # Lane M's fourteen and lane F's nine Flow-card controls; lane P's map controls are its own.
        self.assertEqual(len(v3_controls()), 23)

    def test_a_dual_axis_and_a_shape_card_save_and_read_back_byte_intact(self):
        for name in ("views_save_marks_dual", "views_save_marks_shape"):
            payload = EXAMPLES[name]
            status, body = self.request("/api/studio/views", payload)
            self.assertEqual(status, 200, (name, body))
            stored = self.stored(body["id"])
            self.assertEqual(json.dumps(stored["sheets"]), json.dumps(payload["sheets"]), name)
            status, got = self.request("/api/studio/views?id=" + body["id"])
            self.assertEqual(status, 200)
            self.assertEqual(json.dumps(got["view"]["sheets"]), json.dumps(payload["sheets"]), name)
        status, got = self.request("/api/studio/views?id=" + body["id"])
        self.assertEqual(sorted(got["view"]), sorted(EXAMPLES["views_get_marks_dual"]))

    def test_a_v2_sheet_is_stored_exactly_as_it_always_was(self):
        payload = copy.deepcopy(EXAMPLES["views_save"])
        payload["sheets"] = [EXAMPLES["marks_v2_written"]]
        status, body = self.request("/api/studio/views", payload)
        self.assertEqual(status, 200, body)
        stored = self.stored(body["id"])["sheets"][0]
        self.assertNotIn("marksCards", stored)
        self.assertEqual(json.dumps(stored), json.dumps(EXAMPLES["marks_v2_sheet"]))

    def test_every_malformed_card_set_is_refused_with_one_sentence_and_nothing_is_stored(self):
        for control in v3_controls():
            with self.subTest(why=control["why"][:70]):
                status, body = self.request("/api/studio/views", control["payload"])
                self.assertEqual(status, 400, control["why"])
                self.assertEqual(body, {"error": REFUSAL}, control["why"])
        self.assertEqual(self.request("/api/studio/views")[1], {"views": []})

    def test_the_negative_controls_have_teeth(self):
        # Each control is one fault away from an example the server accepts.
        for name in ("views_save_marks_dual", "views_save_marks_shape"):
            self.assertEqual(self.request("/api/studio/views", EXAMPLES[name])[0], 200, name)

    def test_the_validator_reads_rows_the_way_the_browser_does(self):
        sheet = copy.deepcopy(EXAMPLES["views_save_marks_dual"]["sheets"][0])
        sheet["rows"] = [11, "13"]  # pill() reads a bare index and a numeric string as SUM
        app.validate_view_marks([sheet])
        sheet["rows"] = [11]
        with self.assertRaises(ValueError):
            app.validate_view_marks([sheet])
        card = sheet["marksCards"][0]
        card["opacity"] = 85.0  # JSON 85.0 is an integer to Number.isInteger
        sheet["rows"] = [11, 13]
        app.validate_view_marks([sheet])
        card["opacity"] = True
        with self.assertRaises(ValueError):
            app.validate_view_marks([sheet])


if __name__ == "__main__":
    unittest.main()
