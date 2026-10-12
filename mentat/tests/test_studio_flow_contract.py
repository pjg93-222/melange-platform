"""studio_api.json, lane F (board #46): a Flow card sent to a real Console over HTTP.

The contract's Flow examples were produced by the browser's own writers (chart-studio-core.js
flowSwitch / setFlowOptions / writeCards; laneF gen_studio_flow.js checks them against the sheet the
real client saved on console 8853, and app/tests/studio-flow.test.js asserts the producers again).
Here the same file is what the server is asked to accept: the Flow view saves and reads back
byte-intact, and every Flow-card control (lane F) is refused with one sentence before anything
reaches disk. The negative control patches the server's Flow rule away and shows a control saving.

Same harness as test_studio_marks_contract.py: a temporary data root and an empty report folder;
nothing real is touched. Run from mentat/tests.
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
STUDIO_API = json.loads((CONTRACTS / "studio_api.json").read_text(encoding="utf-8"))
EXAMPLES = STUDIO_API["examples"]
REFUSAL = "The saved view's marks cards are malformed."
FLOW_CONTROLS = [control for control in STUDIO_API["negative_controls"] if control.get("lane") == "F"]

spec = importlib.util.spec_from_file_location("studio_flow_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class StudioFlowContractTests(unittest.TestCase):
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

    def test_the_contract_carries_the_flow_examples_and_nine_controls(self):
        for name in ("marks_card_flow", "views_save_marks_flow", "views_get_marks_flow"):
            self.assertIn(name, EXAMPLES)
        self.assertEqual(len(FLOW_CONTROLS), 9)
        self.assertEqual(EXAMPLES["marks_card_flow"]["type"], "flow")
        self.assertEqual(set(EXAMPLES["marks_card_flow"]["flow"]), {"ribbons", "order", "opacity", "groupAfter"})

    def test_the_flow_view_saves_and_reads_back_byte_intact(self):
        payload = EXAMPLES["views_save_marks_flow"]
        status, body = self.request("/api/studio/views", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.dumps(self.stored(body["id"])["sheets"]), json.dumps(payload["sheets"]))
        status, got = self.request("/api/studio/views?id=" + body["id"])
        self.assertEqual(status, 200)
        self.assertEqual(json.dumps(got["view"]["sheets"]), json.dumps(payload["sheets"]))
        self.assertEqual(sorted(got["view"]), sorted(EXAMPLES["views_get_marks_flow"]))

    def test_every_flow_card_control_is_refused_with_one_sentence_and_nothing_is_stored(self):
        for control in FLOW_CONTROLS:
            with self.subTest(why=control["why"][:70]):
                status, body = self.request("/api/studio/views", control["payload"])
                self.assertEqual((status, body), (400, {"error": REFUSAL}), control["why"])
        self.assertEqual(self.request("/api/studio/views")[1], {"views": []})

    def test_negative_control_without_the_flow_rule_a_malformed_flow_card_saves(self):
        rainbow = next(control for control in FLOW_CONTROLS if "rainbow" in control["why"])
        with patch.object(app, "_flow_ok", lambda card: True):
            status, _ = self.request("/api/studio/views", rainbow["payload"])
        self.assertEqual(status, 200, "the Flow rule is what refuses the control")
        self.assertEqual(self.request("/api/studio/views", rainbow["payload"])[0], 400)


if __name__ == "__main__":
    unittest.main()
