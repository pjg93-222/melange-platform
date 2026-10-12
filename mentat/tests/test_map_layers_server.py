"""Lane P (boards #47-#55): the console side of maps whose marks cards are layers.

Sent to a real Console over HTTP (lane M's harness: a temporary data root, an
empty report folder, nothing real touched):
  - HTTP Range for the offline street tiles: 206 with Content-Range and
    Accept-Ranges, 416 with `bytes */size` past the end, the whole file with 200
    when there is no Range (or one this server does not serve, or a stale
    If-Range), and never more bytes than were asked for (pmtiles refuses that);
  - the two whitelisted routes, /geo/v2/... and /basemap/..., answering only
    the names their patterns allow (a traversal is 404), with the glyph path's
    spaces decoded;
  - the security policy: worker-src and connect-src stated as 'self', nothing
    else moved;
  - studio_api.json's map examples saved and read back byte-intact, and every
    lane P negative control refused with its own sentence before anything is
    stored (DE-021: the fixture both suites import, asserted against the
    producers by app/tests/map-layers-contract.test.js);
  - evidence_geo's per-request dependency scan no longer signs geo/v2.
Run from mentat/tests.
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
CARDS = "The saved view's marks cards are malformed."
MAP = "The saved view's map settings are malformed."

spec = importlib.util.spec_from_file_location("map_layers_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def lane_p_controls():
    return [control for control in STUDIO_API["negative_controls"] if control.get("lane") == "P"]


class ByteRangeTests(unittest.TestCase):
    def test_the_ranges_pmtiles_asks_for(self):
        self.assertEqual(app.byte_range("bytes=0-16383", 100000), (0, 16383))
        self.assertEqual(app.byte_range("bytes=99990-", 100000), (99990, 99999))
        self.assertEqual(app.byte_range("bytes=-10", 100000), (99990, 99999))
        self.assertEqual(app.byte_range("bytes=0-999999", 100), (0, 99), "a range past the end is cut to the file")
        self.assertEqual(app.byte_range(" bytes = 5 - 9 ", 100), (5, 9))

    def test_unsatisfiable_and_ignored_ranges(self):
        self.assertEqual(app.byte_range("bytes=100-", 100), "unsatisfiable")
        self.assertEqual(app.byte_range("bytes=-0", 100), "unsatisfiable")
        self.assertEqual(app.byte_range("bytes=0-1", 0), "unsatisfiable")
        for header in (None, "", "bytes=-", "bytes=9-5", "items=0-5", "bytes=0-1,5-6", "bytes=a-b"):
            self.assertIsNone(app.byte_range(header, 100), header)


class MapRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.data_root, cls.report_root, cls.basemap = root / "data", root / "reports", root / "basemap"
        for folder in (cls.data_root, cls.report_root, cls.basemap / "tiles", cls.basemap / "fonts" / "Noto Sans Regular",
                       cls.basemap / "style", cls.basemap / "vendor" / "maplibre-gl-6.11.2"):
            folder.mkdir(parents=True)
        cls.tiles = bytes(range(256)) * 400                     # 102,400 known bytes
        (cls.basemap / "tiles" / "test-z1.pmtiles").write_bytes(cls.tiles)
        (cls.basemap / "fonts" / "Noto Sans Regular" / "0-255.pbf").write_bytes(b"\x0a\x03abc")
        (cls.basemap / "style" / "light.json").write_text('{"version": 8, "layers": []}', encoding="utf-8")
        (cls.basemap / "vendor" / "maplibre-gl-6.11.2" / "maplibre-gl.mjs").write_text("export const x = 1;\n", encoding="utf-8")
        cls.patches = [patch.object(app, "BASEMAP_DIR", cls.basemap)]
        for item in cls.patches:
            item.start()
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
        for item in cls.patches:
            item.stop()
        cls.temp.cleanup()

    def setUp(self):
        views = self.console.root / "studio" / "views"
        if views.is_dir():
            for path in views.glob("*.json"):
                path.unlink()

    def request(self, path, headers=None, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        sent = {"Host": "127.0.0.1:{}".format(self.server.server_port)}
        sent.update(headers or {})
        body = None
        if payload is not None:
            sent.update({"X-Mentat-Token": self.console.token, "Content-Type": "application/json"})
            body = json.dumps(payload).encode()
        try:
            connection.request("POST" if payload is not None else "GET", path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
        finally:
            connection.close()

    # ------------------------------------------------------------ Range

    def test_a_single_range_is_206_with_content_range_and_exactly_those_bytes(self):
        status, headers, body = self.request("/basemap/tiles/test-z1.pmtiles", {"Range": "bytes=0-16383"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["content-range"], "bytes 0-16383/102400")
        self.assertEqual(headers["accept-ranges"], "bytes")
        self.assertEqual(headers["content-length"], "16384")
        self.assertEqual(body, self.tiles[:16384])
        self.assertNotIn("content-encoding", headers, "a byte range is never compressed")
        status, headers, body = self.request("/basemap/tiles/test-z1.pmtiles", {"Range": "bytes=-100"})
        self.assertEqual((status, headers["content-range"], body), (206, "bytes 102300-102399/102400", self.tiles[-100:]))

    def test_past_the_end_is_416_with_the_size(self):
        status, headers, body = self.request("/basemap/tiles/test-z1.pmtiles", {"Range": "bytes=200000-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["content-range"], "bytes */102400")
        self.assertEqual(body, b"")

    def test_no_range_or_a_stale_if_range_is_the_whole_file(self):
        status, headers, body = self.request("/basemap/tiles/test-z1.pmtiles")
        self.assertEqual((status, len(body)), (200, len(self.tiles)))
        self.assertEqual(headers["accept-ranges"], "bytes")
        tag = headers["etag"]
        status, _, body = self.request("/basemap/tiles/test-z1.pmtiles", {"Range": "bytes=0-9", "If-Range": '"stale"'})
        self.assertEqual((status, len(body)), (200, len(self.tiles)))
        status, _, body = self.request("/basemap/tiles/test-z1.pmtiles", {"Range": "bytes=0-9", "If-Range": tag})
        self.assertEqual((status, body), (206, self.tiles[:10]))

    def test_the_negative_control_a_plain_static_route_ignores_range(self):
        # The teeth: the ordinary static route (what http.server offers) answers
        # 200 with the whole body to the same header, which pmtiles refuses.
        status, headers, body = self.request("/map-layers.js", {"Range": "bytes=0-9"})
        self.assertEqual(status, 200)
        self.assertNotIn("content-range", headers)
        self.assertGreater(len(body), 10)

    # ----------------------------------------------------------- routes

    def test_only_whitelisted_names_are_served(self):
        self.assertEqual(self.request("/basemap/fonts/Noto%20Sans%20Regular/0-255.pbf")[0], 200)
        self.assertEqual(self.request("/basemap/fonts/Noto%20Sans%20Regular/0-255.pbf")[1]["content-type"], "application/x-protobuf")
        self.assertEqual(self.request("/basemap/style/light.json")[0], 200)
        self.assertEqual(self.request("/basemap/vendor/maplibre-gl.mjs")[1]["content-type"], "text/javascript")
        for path in ("/basemap/fonts/Noto%20Sans%20Bold/0-255.pbf", "/basemap/tiles/../style/light.json",
                     "/basemap/tiles/%2e%2e/style/light.json", "/basemap/vendor/../../server.py", "/basemap/secret.txt",
                     "/geo/v2/../../server.py", "/geo/v2/albers/../../server.py", "/geo/v2/other/x.json", "/geo/v2/albers/national/x.py",
                     "/geo/v2/albers/../../../index.json", "/geo/v2/albers/..%2F..%2Fserver.json",
                     "/basemap/fonts/Noto%20Sans%20Regular/..%2F..%2F..%2Fserver.pbf"):
            # "/geo/v2/albers/../../../index.json" fits the route's pattern; its ".." check refuses it.
            self.assertEqual(self.request(path)[0], 404, path)

    @unittest.skipUnless((APP / "geo" / "v2" / "albers" / "national" / "state.json").is_file(), "geo/v2 not installed")
    def test_the_installed_v2_layers_are_served_with_a_validator(self):
        status, headers, body = self.request("/geo/v2/albers/national/state.json", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertTrue(headers["etag"])
        again = self.request("/geo/v2/albers/national/state.json", {"If-None-Match": headers["etag"]})
        self.assertEqual(again[0], 304)
        self.assertEqual(self.request("/geo/v2/index.json")[0], 200)

    def test_the_policy_states_worker_and_connect_self_and_nothing_else_moved(self):
        for path in ("/", "/map-engine.js", "/basemap/tiles/test-z1.pmtiles", "/api/studio/views"):
            policy = self.request(path, {"Range": "bytes=0-1"} if "pmtiles" in path else None)[1]["content-security-policy"]
            self.assertEqual(policy, "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; "
                                     "worker-src 'self'; connect-src 'self'; frame-ancestors 'none'", path)

    def test_every_map_asset_is_registered_and_on_disk(self):
        for name in ("map-layers.js", "map-engine.js", "map-studio.js", "map-streets.mjs", "map-evidence.js", "map.css"):
            self.assertIn("/" + name, app.STATIC_ASSETS)
            self.assertTrue((APP / name).is_file(), name)
            self.assertEqual(self.request("/" + name)[0], 200, name)

    # ------------------------------------------------------ saved views

    def test_the_map_examples_save_and_read_back_byte_intact(self):
        for name in ("views_save_map_layers", "views_save_map_one", "views_save_map_streets", "views_save_map_opposition"):
            payload = EXAMPLES[name]
            status, _, body = self.request("/api/studio/views", payload=payload)
            self.assertEqual(status, 200, (name, body))
            identifier = json.loads(body)["id"]
            stored = json.loads((self.console.root / "studio" / "views" / (identifier + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(json.dumps(stored["sheets"]), json.dumps(payload["sheets"]), name)
            status, _, got = self.request("/api/studio/views?id=" + identifier)
            self.assertEqual(json.dumps(json.loads(got)["view"]["sheets"]), json.dumps(payload["sheets"]), name)

    def test_every_lane_p_control_is_refused_with_its_own_sentence_and_nothing_is_stored(self):
        controls = lane_p_controls()
        self.assertEqual(len(controls), 19)
        for control in controls:
            with self.subTest(why=control["why"][:70]):
                status, _, body = self.request("/api/studio/views", payload=control["payload"])
                self.assertEqual(status, 400, control["why"])
                self.assertEqual(json.loads(body), {"error": CARDS if control["refusal"] == "cards" else MAP})
        self.assertEqual(json.loads(self.request("/api/studio/views")[2]), {"views": []})

    def test_the_validator_directly(self):
        sheet = copy.deepcopy(EXAMPLES["views_save_map_layers"]["sheets"][0])
        app.validate_view_marks([sheet])
        sheet["marksCards"][2]["source"]["cycle"] = 2024           # the DE-021 shape: a number for text
        with self.assertRaises(ValueError):
            app.validate_view_marks([sheet])
        legacy = {"name": "Legacy map", "map": {"layers": ["states", "districts"], "congress": "119"}}
        app.validate_view_marks([legacy])                          # today's boundary choices still save

    def test_one_side_and_the_hatch_are_mirrored_on_save(self):
        # #55's Opposition: the Spent source's oppose side, hatched; the server accepts exactly what
        # map-layers.js accepts, and a malformed side or pattern is refused before anything is stored.
        sheet = copy.deepcopy(EXAMPLES["views_save_map_opposition"]["sheets"][0])
        app.validate_view_marks([sheet])
        hatch = sheet["marksCards"][0]
        self.assertEqual((hatch["source"]["stance"], hatch["map"]["pattern"]), ("oppose", "hatch"))
        for mutate in (lambda card: card["source"].update(stance=["oppose"]),        # an unhashable side is refused, not a crash
                       lambda card: card["source"].update(stance="net"),
                       lambda card: card["source"].update(direction="raised"),
                       lambda card: card["map"].update(pattern={"kind": "hatch"}),
                       lambda card: card.update(type="circle"),
                       lambda card: card.update(type="automatic", geography="point")):
            broken = copy.deepcopy(sheet)
            mutate(broken["marksCards"][0])
            with self.assertRaises(ValueError):
                app.validate_view_marks([broken])
        automatic = copy.deepcopy(sheet)
        automatic["marksCards"][0]["type"] = "automatic"          # Automatic fills a district, so it may be hatched
        app.validate_view_marks([automatic])


class DependencyScanTests(unittest.TestCase):
    def test_the_pac_map_no_longer_signs_geo_v2(self):
        import evidence_geo   # importable: loading server.py put mentat/app on sys.path
        seen = []

        def record(paths):
            seen.append([str(path) for path in paths])
            return ("signature", len(paths))
        with tempfile.TemporaryDirectory() as root, \
                patch.object(evidence_geo, "file_signature", side_effect=record), \
                patch.object(evidence_geo, "geography", side_effect=ValueError("stop")):
            with self.assertRaises(ValueError):
                evidence_geo.cached_geography("snapshot", "fairshake-20260908", "C00835959", "2024", "spent",
                                              snapshot_root=root)
        self.assertTrue(seen, "the scan ran")
        first = seen[0]
        self.assertTrue(any(path.endswith("cd119.json") for path in first), "district boundaries are still signed")
        self.assertFalse([path for path in first if "\\v2\\" in path or "/v2/" in path], "geo/v2 is not signed on every request")


if __name__ == "__main__":
    unittest.main()
