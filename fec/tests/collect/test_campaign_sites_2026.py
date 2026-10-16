"""Offline tests for the 2026 campaign-site collector."""

import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.collect import campaign_sites_2026 as campaign_sites


RUN_UTC = "2026-08-17T12:00:00+00:00"
SIMPLE_HTML = b"<html><body>Public campaign page.</body></html>"


class FetchStub:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        result = self.responses.get(url, (200, SIMPLE_HTML))
        if isinstance(result, Exception):
            raise result
        # Fixtures may give (status, raw) or (status, raw, final_url); production returns 5 values.
        return tuple(result) + (url, "utf-8", False)[len(result) - 2:]


class FakeRobotParser:
    def __init__(self, robots_url, allowed=True, delay=None):
        self.robots_url = robots_url
        self.allowed = allowed
        self.delay = delay
        self.checks = []

    def can_fetch(self, user_agent, url):
        self.checks.append((user_agent, url))
        return self.allowed

    def crawl_delay(self, user_agent):
        del user_agent
        return self.delay


def robot_factory(allowed=True, delay=None):
    return lambda robots_url: FakeRobotParser(robots_url, allowed=allowed, delay=delay)


class CampaignSites2026Tests(unittest.TestCase):
    def make_collector(self, out_dir, fetch=None, allowed=True, max_pages=1):
        return campaign_sites.CampaignSiteCollector(
            out_dir=out_dir,
            fetch_fn=fetch or FetchStub(),
            robot_parser_factory=robot_factory(allowed),
            delay_min=0,
            delay_max=0,
            timeout=1,
            max_pages=max_pages,
        )

    @staticmethod
    def candidate_row(url, name="SAMPLE New"):
        return {
            "state": "MN",
            "district": "09",
            "candidate_name": name,
            "party": "DEMO",
            "incumbent_bioguide": "",
            "url": url,
            "official_url": "",
            "notes": "",
        }

    def test_slug_generation(self):
        first_identity = "MN|05|Jane Q. Doe"
        self.assertEqual(
            campaign_sites.candidate_slug("MN", "05", "Jane Q. Doe"),
            "mn-05-doe-jane-"
            + hashlib.sha256(first_identity.encode("utf-8")).hexdigest()[:6],
        )
        second_identity = "CA|12|O'Neil, Pat"
        self.assertEqual(
            campaign_sites.candidate_slug("CA", "12", "O'Neil, Pat"),
            "ca-12-o-neil-pat-"
            + hashlib.sha256(second_identity.encode("utf-8")).hexdigest()[:6],
        )

    def test_link_role_classification(self):
        for text in ("About", "Meet the Candidate", "Biography"):
            self.assertEqual(campaign_sites.classify_link_role(text), "bio")
        for text in (
            "Issues",
            "Our Priorities",
            "Policy Positions",
            "Where I Stand",
            "Platform",
        ):
            self.assertEqual(campaign_sites.classify_link_role(text), "issues")
        self.assertEqual(campaign_sites.classify_link_role("Donate"), "other")

    def test_platform_point_splitting(self):
        fixture = b"""
            <html><body>
              <h2>Economy</h2><p>Grow jobs.</p>
              <ul><li>Cut red tape.</li></ul>
              <strong>Health</strong><p>Lower costs.</p>
              <script>secret_script_text</script>
              <style>secret_style_text</style>
            </body></html>
        """
        text, points = campaign_sites.extract_text_and_platform_points(
            fixture, include_platform_points=True
        )
        self.assertEqual(
            points,
            [
                {"heading": "Economy", "text": "Grow jobs."},
                {"heading": "Economy", "text": "Cut red tape."},
                {"heading": "Health", "text": "Lower costs."},
            ],
        )
        self.assertNotIn("secret_script_text", text)
        self.assertNotIn("secret_style_text", text)

    def test_revision_diff_does_not_rewrite_identical_raw(self):
        with tempfile.TemporaryDirectory() as out_dir:
            fetch = FetchStub()
            collector = self.make_collector(out_dir, fetch=fetch)
            row = {
                "state": "MN",
                "district": "01",
                "candidate_name": "SAMPLE Alpha",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": "https://example.com/",
                "official_url": "",
                "notes": "",
            }

            first = collector.process_row(row, RUN_UTC)
            second = collector.process_row(row, RUN_UTC)

            self.assertTrue(first["pages"][0]["stored"])
            self.assertFalse(second["pages"][0]["stored"])
            raw_dir = os.path.join(out_dir, "raw", first["slug"])
            self.assertEqual(len(os.listdir(raw_dir)), 1)
            raw_path = os.path.join(raw_dir, os.listdir(raw_dir)[0])
            with gzip.open(raw_path, "rb") as handle:
                self.assertEqual(handle.read(), SIMPLE_HTML)

    def test_manifest_page_schema_and_hash(self):
        with tempfile.TemporaryDirectory() as out_dir:
            collector = self.make_collector(out_dir)
            row = {
                "state": "MN",
                "district": "02",
                "candidate_name": "SAMPLE Alpha",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": "https://example.com/",
                "official_url": "",
                "notes": "",
            }
            manifest = collector.process_row(row, RUN_UTC)

            self.assertEqual(set(manifest), set(campaign_sites.MANIFEST_KEYS))
            required_page_keys = {
                "url",
                "final_url",
                "fetched_utc",
                "http_status",
                "sha256_raw",
                "role",
                "stored",
                "housegov",
                "disallowed_redirect",
                "suspected_login",
                "truncated",
            }
            self.assertTrue(required_page_keys.issubset(manifest["pages"][0]))
            self.assertEqual(
                manifest["pages"][0]["sha256_raw"],
                hashlib.sha256(SIMPLE_HTML).hexdigest(),
            )
            self.assertTrue(manifest["pages"][0]["fetched_utc"].endswith("+00:00"))
            self.assertEqual(manifest["pages"][0]["url"], "https://example.com/")
            self.assertEqual(
                manifest["pages"][0]["final_url"], "https://example.com/"
            )
            manifest_path = os.path.join(
                out_dir, "manifests", manifest["slug"] + ".json"
            )
            with open(manifest_path, encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(set(persisted), set(campaign_sites.MANIFEST_KEYS))
            self.assertTrue(required_page_keys.issubset(persisted["pages"][0]))

    def test_smoke_limit_ignores_no_url_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            roster_path = os.path.join(temp_dir, "roster.csv")
            out_dir = os.path.join(temp_dir, "output")
            fieldnames = [
                "state",
                "district",
                "candidate_name",
                "party",
                "incumbent_bioguide",
                "url",
                "official_url",
                "notes",
            ]
            rows = [
                {
                    "state": "MN",
                    "district": "00",
                    "candidate_name": "SAMPLE None",
                    "party": "DEMO",
                    "incumbent_bioguide": "",
                    "url": "",
                    "official_url": "",
                    "notes": "",
                },
                *[
                    {
                        "state": "MN",
                        "district": str(index),
                        "candidate_name": "SAMPLE Candidate{}".format(index),
                        "party": "DEMO",
                        "incumbent_bioguide": "",
                        "url": "https://example.com/{}/".format(index),
                        "official_url": "",
                        "notes": "",
                    }
                    for index in range(1, 4)
                ],
            ]
            with open(roster_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            fetch = FetchStub()
            with mock.patch.object(campaign_sites, "emit_glassbox"):
                result = campaign_sites.main(
                    [
                        "--roster",
                        roster_path,
                        "--out-dir",
                        out_dir,
                        "--smoke",
                        "2",
                        "--delay-min",
                        "0",
                        "--delay-max",
                        "0",
                        "--max-pages",
                        "1",
                    ],
                    fetch_fn=fetch,
                    robot_parser_factory=robot_factory(True),
                )

            self.assertEqual(result, 0)
            self.assertEqual(len(fetch.calls), 2)
            manifests = os.listdir(os.path.join(out_dir, "manifests"))
            self.assertEqual(len(manifests), 3)

    def test_skipped_no_url_and_housegov_routing(self):
        with tempfile.TemporaryDirectory() as out_dir:
            fetch = FetchStub()
            collector = self.make_collector(out_dir, fetch=fetch)
            no_url = {
                "state": "MN",
                "district": "03",
                "candidate_name": "SAMPLE Gamma",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": "",
                "official_url": "",
                "notes": "",
            }
            official = {
                "state": "MN",
                "district": "04",
                "candidate_name": "SAMPLE Beta",
                "party": "DEMO",
                "incumbent_bioguide": "B000000",
                "url": "",
                "official_url": "https://member.house.gov/official/",
                "notes": "",
            }
            nongovernment_official = {
                "state": "MN",
                "district": "06",
                "candidate_name": "SAMPLE Epsilon",
                "party": "DEMO",
                "incumbent_bioguide": "B000001",
                "url": "",
                "official_url": "https://example.com/official/",
                "notes": "",
            }

            skipped = collector.process_row(no_url, RUN_UTC)
            routed = collector.process_row(official, RUN_UTC)
            nongovernment = collector.process_row(nongovernment_official, RUN_UTC)

            self.assertEqual(skipped["status"], "skipped_no_url")
            self.assertEqual(routed["status"], "collected")
            self.assertTrue(routed["pages"])
            self.assertTrue(all(page["housegov"] for page in routed["pages"]))
            self.assertEqual(
                routed["pages"][0]["url"], "https://member.house.gov/official/"
            )
            self.assertTrue(nongovernment["pages"])
            self.assertTrue(
                all(not page["housegov"] for page in nongovernment["pages"])
            )

    def test_robots_disallowed_skips_without_page_fetch(self):
        with tempfile.TemporaryDirectory() as out_dir:
            fetch = FetchStub()
            collector = self.make_collector(out_dir, fetch=fetch, allowed=False)
            row = {
                "state": "MN",
                "district": "05",
                "candidate_name": "SAMPLE Delta",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": "https://example.com/",
                "official_url": "",
                "notes": "",
            }

            manifest = collector.process_row(row, RUN_UTC)

            self.assertEqual(manifest["status"], "skipped_robots")
            self.assertEqual(fetch.calls, [])
            self.assertEqual(manifest["pages"], [])

    def test_private_literal_target_is_unverified_without_fetch(self):
        with tempfile.TemporaryDirectory() as out_dir:
            fetch = FetchStub()
            collector = self.make_collector(out_dir, fetch=fetch)
            row = {
                "state": "MN",
                "district": "05",
                "candidate_name": "SAMPLE Private",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": "http://127.0.0.1/private",
                "official_url": "",
                "notes": "",
            }

            manifest = collector.process_row(row, RUN_UTC)

            self.assertEqual(manifest["status"], "skipped_unverified")
            self.assertIn("loopback", manifest["error"])
            self.assertEqual(fetch.calls, [])

    def test_login_and_truncation_flags_preserve_raw(self):
        start_url = "https://example.com/start"
        final_url = "https://example.com/auth/login"
        raw = "<html><body>caf\xe9</body></html>".encode("windows-1252")
        fetch = FetchStub(
            {start_url: (200, raw, final_url, "windows-1252", True)}
        )
        with tempfile.TemporaryDirectory() as out_dir:
            collector = self.make_collector(out_dir, fetch=fetch)
            row = {
                "state": "MN",
                "district": "05",
                "candidate_name": "SAMPLE Login",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": start_url,
                "official_url": "",
                "notes": "",
            }

            manifest = collector.process_row(row, RUN_UTC)

            page = manifest["pages"][0]
            self.assertTrue(page["suspected_login"])
            self.assertTrue(page["truncated"])
            self.assertTrue(page["stored"])

    def test_robots_cache_is_keyed_by_scheme_and_host(self):
        created = []

        def scheme_factory(robots_url):
            parser = FakeRobotParser(
                robots_url, allowed=robots_url.startswith("https://")
            )
            created.append(parser)
            return parser

        with tempfile.TemporaryDirectory() as out_dir:
            collector = campaign_sites.CampaignSiteCollector(
                out_dir=out_dir,
                fetch_fn=FetchStub(),
                robot_parser_factory=scheme_factory,
                delay_min=0,
                delay_max=0,
            )
            self.assertTrue(collector._robots_allowed("https://example.com/page"))
            self.assertFalse(collector._robots_allowed("http://example.com/page"))
            self.assertEqual(len(created), 2)
            self.assertIn(("https", "example.com"), collector._robot_cache)
            self.assertIn(("http", "example.com"), collector._robot_cache)

    def test_disallowed_cross_host_redirect_is_not_stored(self):
        start_url = "https://example.com/"
        final_url = "https://blocked.example/page"
        fetch = FetchStub({start_url: (200, SIMPLE_HTML, final_url)})

        def redirect_robot_factory(robots_url):
            return FakeRobotParser(
                robots_url, allowed="blocked.example" not in robots_url
            )

        with tempfile.TemporaryDirectory() as out_dir:
            collector = campaign_sites.CampaignSiteCollector(
                out_dir=out_dir,
                fetch_fn=fetch,
                robot_parser_factory=redirect_robot_factory,
                delay_min=0,
                delay_max=0,
                max_pages=1,
            )
            row = {
                "state": "MN",
                "district": "07",
                "candidate_name": "SAMPLE Redirect",
                "party": "DEMO",
                "incumbent_bioguide": "",
                "url": start_url,
                "official_url": "",
                "notes": "",
            }

            manifest = collector.process_row(row, RUN_UTC)

            self.assertEqual(manifest["status"], "skipped_robots")
            page = manifest["pages"][0]
            self.assertEqual(page["final_url"], final_url)
            self.assertTrue(page["disallowed_redirect"])
            self.assertFalse(page["stored"])
            self.assertFalse(
                os.path.exists(os.path.join(out_dir, "raw", manifest["slug"]))
            )

    def test_sweep_requires_approval_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            roster_path = os.path.join(temp_dir, "roster.csv")
            out_dir = os.path.join(temp_dir, "output")
            with open(roster_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=campaign_sites.EXPECTED_ROSTER_COLUMNS
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "state": "MN",
                        "district": "08",
                        "candidate_name": "SAMPLE Sweep",
                        "party": "DEMO",
                        "incumbent_bioguide": "",
                        "url": "https://example.com/",
                        "official_url": "",
                        "notes": "",
                    }
                )
            fetch = FetchStub()
            arguments = [
                "--roster",
                roster_path,
                "--out-dir",
                out_dir,
                "--sweep",
                "--delay-min",
                "0",
                "--delay-max",
                "0",
                "--max-pages",
                "1",
            ]
            with mock.patch.object(campaign_sites, "emit_glassbox"):
                refused = campaign_sites.main(
                    arguments,
                    fetch_fn=fetch,
                    robot_parser_factory=robot_factory(True),
                )
            self.assertNotEqual(refused, 0)
            self.assertEqual(fetch.calls, [])

            os.makedirs(out_dir, exist_ok=True)
            with open(
                os.path.join(out_dir, "SWEEP_APPROVED"), "w", encoding="utf-8"
            ):
                pass
            with mock.patch.object(campaign_sites, "emit_glassbox"):
                accepted = campaign_sites.main(
                    arguments,
                    fetch_fn=fetch,
                    robot_parser_factory=robot_factory(True),
                )
            self.assertEqual(accepted, 0)
            self.assertEqual(len(fetch.calls), 1)

    def test_default_fetcher_delay_bounds_have_two_second_floor(self):
        with tempfile.TemporaryDirectory() as out_dir:
            collector = campaign_sites.CampaignSiteCollector(
                out_dir=out_dir,
                robot_parser_factory=robot_factory(True),
                delay_min=0,
                delay_max=0,
            )
            lower, upper = collector.effective_delay_bounds()
            self.assertGreaterEqual(lower, 2.0)
            self.assertGreaterEqual(upper, 2.0)
            crawl_lower, crawl_upper = collector.effective_delay_bounds(3.5)
            self.assertGreaterEqual(crawl_lower, 3.5)
            self.assertGreaterEqual(crawl_upper, 3.5)

    def test_roster_header_must_match_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            roster_path = os.path.join(temp_dir, "bad-roster.csv")
            out_dir = os.path.join(temp_dir, "output")
            bad_columns = list(campaign_sites.EXPECTED_ROSTER_COLUMNS)
            bad_columns[2] = "candiate_name"
            with open(roster_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(bad_columns)
            fetch = FetchStub()
            stderr = io.StringIO()
            with mock.patch.object(campaign_sites, "emit_glassbox"), mock.patch(
                "sys.stderr", stderr
            ):
                result = campaign_sites.main(
                    [
                        "--roster",
                        roster_path,
                        "--out-dir",
                        out_dir,
                        "--smoke",
                        "1",
                    ],
                    fetch_fn=fetch,
                    robot_parser_factory=robot_factory(True),
                )
            self.assertNotEqual(result, 0)
            self.assertIn("expected=", stderr.getvalue())
            self.assertIn("found=", stderr.getvalue())
            self.assertEqual(fetch.calls, [])

    def test_user_agent_contains_contact_address(self):
        self.assertIn("goff0070@umn.edu", campaign_sites.USER_AGENT)

    def test_glassbox_emitter_absence_is_fail_soft(self):
        with mock.patch.object(
            campaign_sites.subprocess,
            "run",
            side_effect=FileNotFoundError(campaign_sites.GLASSBOX_EMITTER),
        ):
            campaign_sites.emit_glassbox(
                "task.started", "running", campaign_sites._empty_summary()
            )

    def test_subpage_failure_is_recorded_and_does_not_sink_the_candidate(self):
        home = (
            b'<html><body><a href="/about">About</a> '
            b'<a href="/issues">Issues</a></body></html>'
        )
        about = "https://example.com/about"
        fetch = FetchStub(
            {
                "https://example.com/": (200, home),
                about: urllib.error.HTTPError(about, 404, "nf", {}, None),
            }
        )
        with tempfile.TemporaryDirectory() as out_dir, mock.patch.object(
            campaign_sites.time, "sleep"
        ):
            collector = self.make_collector(out_dir, fetch=fetch, max_pages=3)
            manifest = collector.process_row(
                self.candidate_row("https://example.com/"), RUN_UTC
            )

        self.assertEqual(manifest["status"], "collected")
        self.assertEqual(len(manifest["page_errors"]), 1)
        self.assertEqual(manifest["page_errors"][0]["url"], about)
        self.assertEqual(len(manifest["pages"]), 2)

    def test_http_error_is_not_retried_or_downgraded_to_http(self):
        page = "https://example.com/page"
        fetch = FetchStub({page: urllib.error.HTTPError(page, 403, "no", {}, None)})
        with tempfile.TemporaryDirectory() as out_dir, mock.patch.object(
            campaign_sites.time, "sleep"
        ):
            collector = self.make_collector(out_dir, fetch=fetch)
            manifest = collector.process_row(
                self.candidate_row("http://example.com/page"), RUN_UTC
            )

        self.assertEqual([call[0] for call in fetch.calls], [page])
        self.assertEqual(manifest["status"], "error")

    def test_certificate_failure_is_not_downgraded_to_http(self):
        page = "https://example.com/page"
        failure = urllib.error.URLError(
            campaign_sites.ssl.SSLCertVerificationError("bad certificate")
        )
        fetch = FetchStub({page: failure})
        with tempfile.TemporaryDirectory() as out_dir, mock.patch.object(
            campaign_sites.time, "sleep"
        ):
            collector = self.make_collector(out_dir, fetch=fetch)
            manifest = collector.process_row(
                self.candidate_row("http://example.com/page"), RUN_UTC
            )

        self.assertTrue(all(call[0] == page for call in fetch.calls))
        self.assertEqual(manifest["status"], "error")

    def test_private_target_reason_covers_reserved_and_alternate_literals(self):
        for host in (
            "0.0.0.0",
            "100.64.0.1",
            "[fd00::1]",
            "[fe80::1]",
            "[::ffff:127.0.0.1]",
            "2130706433",
            "0x7f000001",
            "127.1",
        ):
            self.assertIsNotNone(
                campaign_sites.private_target_reason("http://{}/".format(host)), host
            )
        self.assertIsNone(campaign_sites.private_target_reason("https://example.com/"))

    def test_resolution_check_refuses_names_that_resolve_inward(self):
        inward = [(2, 1, 6, "", ("10.1.2.3", 443))]
        outward = [(2, 1, 6, "", ("93.184.216.34", 443))]
        getaddrinfo = mock.patch.object(campaign_sites.socket, "getaddrinfo")
        with getaddrinfo as patched:
            patched.return_value = inward
            with self.assertRaises(campaign_sites.UnverifiedTarget):
                campaign_sites._assert_public_resolution("https://rebind.example/")
            patched.return_value = outward
            campaign_sites._assert_public_resolution("https://example.com/")
            patched.side_effect = campaign_sites.socket.gaierror("no such host")
            with self.assertRaises(urllib.error.URLError):
                campaign_sites._assert_public_resolution("https://gone.example/")

    def test_crawl_delay_over_the_cap_skips_the_site_without_a_fetch(self):
        fetch = FetchStub()
        with tempfile.TemporaryDirectory() as out_dir:
            collector = campaign_sites.CampaignSiteCollector(
                out_dir=out_dir,
                fetch_fn=fetch,
                robot_parser_factory=robot_factory(True, delay=120),
                delay_min=0,
                delay_max=0,
            )
            manifest = collector.process_row(
                self.candidate_row("https://example.com/"), RUN_UTC
            )

        self.assertEqual(manifest["status"], "skipped_robots")
        self.assertEqual(fetch.calls, [])
        self.assertIn("crawl-delay", manifest["error"])

    def test_unknown_charset_falls_back_to_utf8_and_only_five_values_are_accepted(self):
        url = "https://example.com/"
        for name in ("bogus", "hex", None, ""):
            result = campaign_sites._normalize_fetch_result(
                (200, b"x", url, name, False), url
            )
            self.assertEqual(result["charset"], "utf-8")
        known = campaign_sites._normalize_fetch_result(
            (200, b"x", url, "windows-1252", False), url
        )
        self.assertEqual(known["charset"], "cp1252")
        with self.assertRaises(ValueError):
            campaign_sites._normalize_fetch_result((200, b"x"), url)

    def test_run_log_failure_keeps_the_candidate_status_and_fails_the_exit_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            roster_path = os.path.join(temp_dir, "roster.csv")
            out_dir = os.path.join(temp_dir, "output")
            with open(roster_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=campaign_sites.EXPECTED_ROSTER_COLUMNS
                )
                writer.writeheader()
                writer.writerow(self.candidate_row("https://example.com/"))
            with mock.patch.object(campaign_sites, "emit_glassbox"), mock.patch.object(
                campaign_sites, "_atomic_append_json_line", side_effect=OSError("disk full")
            ), mock.patch("sys.stderr", io.StringIO()):
                result = campaign_sites.main(
                    [
                        "--roster",
                        roster_path,
                        "--out-dir",
                        out_dir,
                        "--smoke",
                        "1",
                        "--delay-min",
                        "0",
                        "--delay-max",
                        "0",
                        "--max-pages",
                        "1",
                    ],
                    fetch_fn=FetchStub(),
                    robot_parser_factory=robot_factory(True),
                )

            self.assertEqual(result, 1)
            manifest_dir = os.path.join(out_dir, "manifests")
            with open(
                os.path.join(manifest_dir, os.listdir(manifest_dir)[0]), encoding="utf-8"
            ) as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["status"], "collected")
            self.assertIn("disk full", persisted["run_log_error"])


if __name__ == "__main__":
    unittest.main()
