# -*- coding: utf-8 -*-
"""Regression tests for the CFPB HMDA curl transport."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import district.fetch_hmda_district as hmda  # noqa: E402
import audit.dashboard_integrity as desk_integrity  # noqa: E402


CSV_PAYLOAD = (
    b"census_tract,action_taken,loan_purpose,"
    b"tract_to_msa_income_percentage\n"
    b"56001000100,1,1,79.9\n"
    b"56001000200,2,31,70\n"
    b"56001000300,1,31,80\n"
)
FINAL_URL = (
    "https://files.ffiec.cfpb.gov/data-browser/datasets/2024/"
    "filtered-queries/one-year/test.csv"
)


def curl_stderr(status=200, final_url=FINAL_URL, diagnostic=""):
    prefix = (diagnostic + "\n") if diagnostic else ""
    return (
        prefix
        + hmda.CURL_META_PREFIX
        + str(status)
        + "\t"
        + final_url
        + "\n"
    ).encode("utf-8")


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._planned_returncode = returncode
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._planned_returncode
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self.returncode = self._planned_returncode

    def kill(self):
        self.returncode = self._planned_returncode


class HmdaCurlTransportTests(unittest.TestCase):
    def throttle(self):
        return {"lock": threading.Lock(), "last_request": None}

    def test_curl_command_is_fail_closed_and_streams_stdout(self):
        command = hmda._curl_command(
            "curl.exe",
            "https://example.test/data.csv",
            byte_range="0-2047",
            max_time=12.5,
            max_filesize=2048,
        )
        self.assertIn("--fail", command)
        self.assertIn("--location", command)
        self.assertEqual(2, command.count("=https"))
        self.assertIn("--range", command)
        self.assertIn("--max-filesize", command)
        self.assertNotIn("--output", command)
        write_out = command[command.index("--write-out") + 1]
        self.assertIn("%{stderr}", write_out)
        self.assertIn("%{http_code}", write_out)
        self.assertIn("%{url_effective}", write_out)

    def test_canary_accepts_valid_partial_csv(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=CSV_PAYLOAD,
            stderr=curl_stderr(status=206),
        )
        with mock.patch.object(hmda.subprocess, "run", return_value=completed):
            cleared, detail = hmda._probe_canary(
                "2024",
                self.throttle(),
                time.monotonic() + 30,
                "curl.exe",
            )
        self.assertTrue(cleared)
        self.assertEqual("HTTP 206 CSV via curl", detail)

    def test_canary_reports_http_403(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=22,
            stdout=b"",
            stderr=curl_stderr(
                status=403,
                diagnostic="curl: (22) HTTP response code said error",
            ),
        )
        with mock.patch.object(hmda.subprocess, "run", return_value=completed):
            cleared, detail = hmda._probe_canary(
                "2024",
                self.throttle(),
                time.monotonic() + 30,
                "curl.exe",
            )
        self.assertFalse(cleared)
        self.assertEqual("HTTP 403 via curl", detail)

    def test_state_collection_aggregates_only_originations(self):
        process = FakeProcess(
            stdout=CSV_PAYLOAD,
            stderr=curl_stderr(),
            returncode=0,
        )
        with mock.patch.object(hmda.subprocess, "Popen", return_value=process):
            result = hmda._collect_state(
                "WY",
                "2024",
                self.throttle(),
                threading.Event(),
                "curl.exe",
            )
        self.assertEqual("ok", result["status"])
        cache = result["cache"]
        self.assertEqual(3, cache["n_lar_rows"])
        self.assertEqual(2, cache["n_originations"])
        self.assertEqual("curl", cache["sources"]["lar"]["transport"])
        self.assertEqual(FINAL_URL, cache["sources"]["lar"]["url"])
        self.assertEqual(2, sum(cell["count"] for cell in cache["aggregates"]))

    def test_state_collection_classifies_403_without_cache(self):
        process = FakeProcess(
            stdout=b"",
            stderr=curl_stderr(
                status=403,
                diagnostic="curl: (22) The requested URL returned error: 403",
            ),
            returncode=22,
        )
        with (
            mock.patch.object(hmda.subprocess, "Popen", return_value=process),
            mock.patch.object(hmda, "STATE_MAX_ATTEMPTS", 1),
        ):
            result = hmda._collect_state(
                "WY",
                "2024",
                self.throttle(),
                threading.Event(),
                "curl.exe",
            )
        self.assertEqual({"status": "failed_403"}, result)

    def test_non_http_curl_failure_is_fail_closed(self):
        process = FakeProcess(
            stdout=b"",
            stderr=b"curl: (6) Could not resolve host\n",
            returncode=6,
        )
        with mock.patch.object(hmda.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(SystemExit, "curl exit 6"):
                hmda._stream_state_with_curl(
                    "curl.exe",
                    "https://example.test/data.csv",
                    "WY",
                    "2024",
                    threading.Event(),
                )

    def test_pre_cancelled_state_never_starts_curl(self):
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch.object(hmda.subprocess, "Popen") as popen:
            result = hmda._collect_state(
                "WY",
                "2024",
                self.throttle(),
                cancelled,
                "curl.exe",
            )
        self.assertEqual({"status": "cancelled"}, result)
        popen.assert_not_called()

    def test_missing_curl_fails_before_collection(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(hmda.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(SystemExit, "curl was not found"):
                hmda._resolve_curl()

    def test_desk_integrity_fingerprints_every_optional_district_layer(self):
        self.assertEqual(
            (
                "data/lobbying/district_industry.json",
                "data/lobbying/district_industry_4digit.json",
                "data/district/district_community.json",
                "data/district/district_employers.json",
                "data/district/district_financial.json",
                "data/district/district_hmda.json",
            ),
            desk_integrity.OPTIONAL_DISTRICT_SOURCES,
        )


if __name__ == "__main__":
    unittest.main()
