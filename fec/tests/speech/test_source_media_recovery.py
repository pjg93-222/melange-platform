import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import datetime as dt
import concurrent.futures
import threading
import time
import types
import wave

from fec.speech import source_media_recovery as SMR


class SourceMediaRecoveryTests(unittest.TestCase):
    def test_asr_conditioning_control_is_receipted_and_invalidates_incompatible_cache(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            wav = root / "source.wav"
            wav.write_bytes(b"synthetic waveform fixture")
            acquisition = {"wav": str(wav), "wav_sha256": SMR.sha256_file(wav)}
            calls = []
            class FakeModel:
                def transcribe(self, audio, **kwargs):
                    calls.append(kwargs)
                    words = [types.SimpleNamespace(word=f" token{i}", start=float(i), end=i + .25,
                                                   probability=.99) for i in range(25)]
                    return [types.SimpleNamespace(words=words)], types.SimpleNamespace(language="en")
            model = FakeModel()
            _, original = SMR.load_or_create_asr_transcript(acquisition, model, None)
            old_bytes = Path(original["transcript"]).read_bytes()
            self.assertTrue(calls[0]["condition_on_previous_text"])
            SMR.load_or_create_asr_transcript(acquisition, model, None)
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(SMR.RecoveryError, "stale or hash-invalid"):
                SMR.load_or_create_asr_transcript({**acquisition, "condition_on_previous_text": False}, model, None)
            self.assertEqual(Path(original["transcript"]).read_bytes(), old_bytes)
            repair = {**acquisition, "condition_on_previous_text": False,
                      "asr_request_settings": {"condition_on_previous_text": False, "language": None},
                      "asr_transcript": str(root / "repair" / "words.jsonl"),
                      "asr_transcript_receipt": str(root / "repair" / "receipt.json")}
            _, receipt = SMR.load_or_create_asr_transcript(repair, model, None)
            self.assertFalse(calls[-1]["condition_on_previous_text"])
            self.assertFalse(receipt["condition_on_previous_text"])
            self.assertEqual(receipt["asr_request_settings"], repair["asr_request_settings"])
            with self.assertRaisesRegex(SMR.RecoveryError, "stale or hash-invalid"):
                SMR.load_or_create_asr_transcript({**repair, "asr_request_settings": {"language": "es"}}, model, None)
            self.assertEqual(len(calls), 2)

    def test_seconds_to_frame_rounds_instead_of_truncating(self):
        self.assertEqual(SMR.seconds_to_frame(7.99996875, 16000), 128000)
        self.assertEqual(SMR.seconds_to_frame(15.99996875, 16000), 256000)

    def test_member_gap_filter_prioritizes_members_without_nonfloor_audio(self):
        targets = {
            "Covered Gap": {
                "status": "NEEDS_MORE", "clips_needed": 2, "minutes_needed": 1.0,
            },
            "Empty Gap": {
                "status": "NO_NONFLOOR_AUDIO", "clips_needed": 5,
                "minutes_needed": 3.0,
            },
        }
        empty = {
            "package_id": "pkg:empty",
            "members": [{"member": "Empty Gap"}],
        }
        partial = {
            "package_id": "pkg:partial",
            "members": [{"member": "Covered Gap"}],
        }
        sufficient = {
            "package_id": "pkg:sufficient",
            "members": [{"member": "Already Sufficient"}],
        }
        self.assertLess(
            SMR.member_gap_priority(empty, targets),
            SMR.member_gap_priority(partial, targets),
        )
        self.assertIsNone(SMR.member_gap_priority(sufficient, targets))

    def test_discover_counts_structured_probe_error_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "inventory.jsonl"
            inventory.write_text(json.dumps({
                "package_id": "pkg:test",
                "source_family": "cnn",
                "discovery": {"status": "needs_probe"},
            }) + "\n", encoding="utf-8")
            args = types.SimpleNamespace(
                inventory=str(inventory), source="cnn", package_id=None,
                retry_blocked=False, max_packages=0, refresh=False,
                cnn_search=False, pause=0, workers=1, checkpoint_every=1,
            )
            result = SMR.media_result(
                "probe_error", "cnn_tvnews_airtime",
                "HTTP Error 429: Too Many Requests",
            )
            with (
                mock.patch.object(SMR, "shared_discovery_state", return_value={}),
                mock.patch.object(SMR, "discover_one", return_value=result),
                mock.patch.object(SMR, "persist", return_value={}),
            ):
                self.assertEqual(SMR.run_discover(args), 2)

    def _cnn_local_reuse_fixture(self, root: Path):
        sotu_root = root / "sunday_audio"
        axe_root = root / "axe_audio"
        asr_root = root / "asr"
        sotu_root.mkdir()
        axe_root.mkdir()

        def make_wav(path: Path, byte_value: int, frames: int = 32000):
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(bytes((byte_value, 0)) * frames)

        dates = ("2024-07-14", "2025-12-14")
        sotu_targets = []
        sotu_log = []
        inventory = []
        for index, date in enumerate(dates, 1):
            wav = sotu_root / f"sotu_{date}.wav"
            make_wav(wav, index)
            sotu_targets.append({
                "fam": "sotu", "date": date,
                "url": f"https://traffic.example.test/sotu-{date}.mp3",
                "title": "NEVER COPY TITLE", "members": ["NEVER COPY MEMBER"],
            })
            sotu_log.append({"sid": f"sotu_{date}", "status": "OK", "bytes": wav.stat().st_size})
            segments = (1, 2) if index == 2 else (1,)
            for segment in segments:
                package = (
                    f"https://transcripts.cnn.com/show/sotu/date/{date}/segment/{segment:02d}"
                )
                inventory.append({
                    "schema": SMR.SCHEMA,
                    "package": package,
                    "package_id": SMR.package_id(package),
                    "source_family": "cnn",
                    "show": "sotu",
                    "date": date,
                    "turn_count": 1,
                    "word_count": 40,
                    "inventory_sha256": SMR.sha256_text("inventory:" + package),
                    "transcript_sha256": SMR.sha256_text("transcript:" + package),
                })

        matched_guid = "ac039188-cdf2-4fc7-b3e5-b00c017d18d0"
        unresolved_guid = "d3bbec0a-9f76-11ee-84b4-6fa019be99c7"
        axe_wav = axe_root / "matched.wav"
        make_wav(axe_wav, 9)
        axe_manifest = [{
            "guid": matched_guid,
            "wav": str(axe_wav.resolve()),
            "title": "NEVER COPY AXE TITLE",
            "member": "NEVER COPY AXE MEMBER",
            "pubDate": "NEVER COPY AIRTIME",
        }]
        axe_log = [{
            "guid": matched_guid, "status": "downloaded", "wav": str(axe_wav.resolve())
        }]
        for guid in (matched_guid, unresolved_guid):
            package = f"https://www.cnn.com/audio/podcasts/axe-files/episodes/{guid}"
            inventory.append({
                "schema": SMR.SCHEMA,
                "package": package,
                "package_id": SMR.package_id(package),
                "source_family": "cnn-axe-files",
                "show": "CNN The Axe Files",
                "date": "2024-01-01",
                "turn_count": 1,
                "word_count": 40,
                "inventory_sha256": SMR.sha256_text("inventory:" + package),
                "transcript_sha256": SMR.sha256_text("transcript:" + package),
            })
        hashes = {
            "inventory": "1" * 64,
            "sotu_targets": "2" * 64,
            "sotu_fetch_log": "3" * 64,
            "axe_manifest": "4" * 64,
            "axe_fetch_log": "5" * 64,
        }
        return {
            "inventory": inventory,
            "sotu_targets": sotu_targets,
            "sotu_log": sotu_log,
            "axe_manifest": axe_manifest,
            "axe_log": axe_log,
            "sotu_root": sotu_root,
            "asr_root": asr_root,
            "hashes": hashes,
        }

    def _build_cnn_local_reuse_fixture(self, root: Path):
        fixture = self._cnn_local_reuse_fixture(root)
        rows, unresolved = SMR.build_cnn_local_reuse_routes(
            fixture["inventory"], fixture["sotu_targets"], fixture["sotu_log"],
            fixture["axe_manifest"], fixture["axe_log"],
            sotu_root=fixture["sotu_root"], asr_root=fixture["asr_root"],
            input_hashes=fixture["hashes"],
            expected_sotu_sources=2, expected_sotu_routes=3,
            expected_axe_sources=1, expected_axe_inventory_packages=2,
        )
        return fixture, rows, unresolved

    def test_cnn_local_reuse_routes_sources_not_identity_hints(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture, rows, unresolved = self._build_cnn_local_reuse_fixture(Path(raw))
            counts = SMR.validate_cnn_local_reuse_routes(
                rows, expected_sources=3, expected_package_routes=4
            )
            self.assertEqual(counts["physical_sources"], 3)
            self.assertEqual(counts["package_routes"], 4)
            self.assertEqual(counts["asr_sources_pending"], 3)
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(
                unresolved[0]["episode_guid"], "d3bbec0a-9f76-11ee-84b4-6fa019be99c7"
            )
            serialized = json.dumps(rows)
            self.assertNotIn("NEVER COPY", serialized)
            self.assertNotIn("members", serialized)
            multi = [row for row in rows if len(row["package_routes"]) == 2]
            self.assertEqual(len(multi), 1)
            self.assertEqual(multi[0]["asr"]["package_route_count"], 2)
            self.assertEqual(len({row["asr"]["receipt"] for row in rows}), 3)
            self.assertTrue(all(
                route["speaker_identity_authority"] is False
                for row in rows for route in row["package_routes"]
            ))
            SMR.validate_cnn_local_reuse_inventory_binding(
                rows, fixture["inventory"], inventory_file_sha256="1" * 64
            )

    def test_cnn_local_reuse_rejects_duplicate_axe_guid(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self._cnn_local_reuse_fixture(Path(raw))
            fixture["axe_manifest"].append(dict(fixture["axe_manifest"][0]))
            with self.assertRaisesRegex(SMR.RecoveryError, "ambiguous or duplicated"):
                SMR.build_cnn_local_reuse_routes(
                    fixture["inventory"], fixture["sotu_targets"], fixture["sotu_log"],
                    fixture["axe_manifest"], fixture["axe_log"],
                    sotu_root=fixture["sotu_root"], asr_root=fixture["asr_root"],
                    input_hashes=fixture["hashes"],
                    expected_sotu_sources=2, expected_sotu_routes=3,
                    expected_axe_sources=1, expected_axe_inventory_packages=2,
                )

    def test_cnn_local_reuse_rejects_missing_sotu_route(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self._cnn_local_reuse_fixture(Path(raw))
            fixture["inventory"] = fixture["inventory"][1:]
            with self.assertRaisesRegex(SMR.RecoveryError, "SOTU package routes"):
                SMR.build_cnn_local_reuse_routes(
                    fixture["inventory"], fixture["sotu_targets"], fixture["sotu_log"],
                    fixture["axe_manifest"], fixture["axe_log"],
                    sotu_root=fixture["sotu_root"], asr_root=fixture["asr_root"],
                    input_hashes=fixture["hashes"],
                    expected_sotu_sources=2, expected_sotu_routes=3,
                    expected_axe_sources=1, expected_axe_inventory_packages=2,
                )

    def test_cnn_local_reuse_asr_runs_once_per_physical_source(self):
        with tempfile.TemporaryDirectory() as raw:
            _, rows, _ = self._build_cnn_local_reuse_fixture(Path(raw))
            calls = []
            state_lock = threading.Lock()
            active = 0
            maximum_active = 0
            model_threads = set()
            checkpoint_threads = []
            checkpoint_orders = []

            class FakeModel:
                def transcribe(self, audio, **kwargs):
                    nonlocal active, maximum_active
                    with state_lock:
                        calls.append(audio)
                        active += 1
                        maximum_active = max(maximum_active, active)
                        model_threads.add(threading.get_ident())
                    try:
                        time.sleep(0.03)
                        words = [
                            types.SimpleNamespace(
                                word=f" token{index}", start=float(index),
                                end=float(index) + 0.25, probability=0.99,
                            )
                            for index in range(25)
                        ]
                        return [types.SimpleNamespace(words=words)], types.SimpleNamespace(language="en")
                    finally:
                        with state_lock:
                            active -= 1

            def checkpoint(current):
                checkpoint_threads.append(threading.get_ident())
                checkpoint_orders.append([row["physical_source_id"] for row in current])

            updated = SMR.transcribe_cnn_local_reuse_sources(
                rows, FakeModel(), model_path=Path(raw) / "model",
                audio_loader=lambda path: path.name,
                checkpoint=checkpoint, workers=3,
                expected_sources=3, expected_package_routes=4,
            )
            self.assertEqual(len(calls), 3)
            self.assertGreaterEqual(maximum_active, 2)
            self.assertEqual(len(checkpoint_orders), 3)
            self.assertTrue(all(order == sorted(order) for order in checkpoint_orders))
            self.assertEqual(checkpoint_threads, [threading.get_ident()] * 3)
            self.assertNotIn(threading.get_ident(), model_threads)
            self.assertEqual(
                [row["physical_source_id"] for row in updated],
                sorted(row["physical_source_id"] for row in updated),
            )
            self.assertEqual(len(updated), 3)
            counts = SMR.validate_cnn_local_reuse_routes(
                updated, expected_sources=3, expected_package_routes=4, require_asr=True
            )
            self.assertEqual(counts["asr_sources_ready"], 3)
            multi = next(row for row in updated if len(row["package_routes"]) == 2)
            self.assertTrue(Path(multi["asr"]["receipt"]).is_file())
            resumed = SMR.transcribe_cnn_local_reuse_sources(
                updated, FakeModel(), model_path=Path(raw) / "model",
                audio_loader=lambda path: path.name,
                workers=3,
                expected_sources=3, expected_package_routes=4,
            )
            self.assertEqual(len(calls), 3, "valid source-level ASR receipts must be resumed, not rerun")
            self.assertEqual(resumed, updated)

            receipt_path = Path(multi["asr"]["receipt"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["speaker_identity_authority"] = True
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            tampered = json.loads(json.dumps(updated))
            changed = next(row for row in tampered if row["physical_source_id"] == multi["physical_source_id"])
            changed["asr"]["receipt_sha256"] = SMR.sha256_file(receipt_path)
            changed["route_row_sha256"] = SMR._route_row_hash(changed)
            with self.assertRaisesRegex(SMR.RecoveryError, "receipt contract"):
                SMR.validate_cnn_local_reuse_routes(
                    tampered, expected_sources=3, expected_package_routes=4, require_asr=True
                )

    def test_cnn_local_reuse_concurrent_failure_cancels_unstarted_and_never_checkpoints_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            _, rows, _ = self._build_cnn_local_reuse_fixture(Path(raw))
            ordered = sorted(rows, key=lambda row: row["physical_source_id"])
            fail_wav = ordered[0]["source"]["wav"]
            barrier = threading.Barrier(2)
            calls = []
            calls_lock = threading.Lock()
            checkpoints = []

            class FailingModel:
                def transcribe(self, audio, **kwargs):
                    with calls_lock:
                        calls.append(audio)
                    barrier.wait(timeout=2)
                    if audio == fail_wav:
                        raise RuntimeError("synthetic source failure")
                    time.sleep(0.08)
                    words = [
                        types.SimpleNamespace(
                            word=f" token{index}", start=float(index),
                            end=float(index) + 0.25, probability=0.99,
                        )
                        for index in range(25)
                    ]
                    return [types.SimpleNamespace(words=words)], types.SimpleNamespace(language="en")

            with self.assertRaisesRegex(SMR.RecoveryError, "synthetic source failure"):
                SMR.transcribe_cnn_local_reuse_sources(
                    rows, FailingModel(), model_path=Path(raw) / "model",
                    audio_loader=lambda path: str(path),
                    checkpoint=lambda current: checkpoints.append(json.loads(json.dumps(current))),
                    workers=2, expected_sources=3, expected_package_routes=4,
                )
            self.assertEqual(len(calls), 2, "the unstarted third source must be cancelled")
            self.assertEqual(checkpoints, [], "a failed completion batch must not be committed")
            self.assertTrue(all(row["asr"]["status"] == "REQUIRED" for row in rows))

    def test_cnn_local_reuse_materializes_hash_bound_acquisitions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture, rows, _ = self._build_cnn_local_reuse_fixture(root)

            class FakeModel:
                def transcribe(self, audio, **kwargs):
                    words = [
                        types.SimpleNamespace(
                            word=f" token{index}", start=float(index),
                            end=float(index) + 0.25, probability=0.99,
                        )
                        for index in range(25)
                    ]
                    return [types.SimpleNamespace(words=words)], types.SimpleNamespace(language="en")

            ready = SMR.transcribe_cnn_local_reuse_sources(
                rows, FakeModel(), model_path=root / "model",
                audio_loader=lambda path: path.name,
                workers=3, expected_sources=3, expected_package_routes=4,
            )
            output = root / "aligned_inventory.jsonl"
            materialized, routed = SMR.materialize_cnn_local_reuse_acquisitions(
                ready, fixture["inventory"],
                inventory_file_sha256=fixture["hashes"]["inventory"],
                output_inventory=output,
                expected_sources=3, expected_package_routes=4,
            )
            self.assertEqual(routed, 4)
            self.assertTrue(output.is_file())
            acquired = [
                row for row in materialized
                if (row.get("acquisition") or {}).get("status") == "acquired"
            ]
            self.assertEqual(len(acquired), 4)
            self.assertTrue(all(len(row["acquisition"]["items"]) == 1 for row in acquired))
            for row in acquired:
                item = row["acquisition"]["items"][0]
                self.assertEqual(item["provider"], "cnn-local-reuse-podcast")
                self.assertFalse(item["source_private"])
                self.assertTrue(Path(item["source_receipt"]).is_file())
                self.assertEqual(item["wav_sha256"], SMR.sha256_file(Path(item["wav"])))
            self.assertTrue(all("acquisition" not in row for row in fixture["inventory"]))

    def test_cnn_local_reuse_inventory_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture, rows, _ = self._build_cnn_local_reuse_fixture(Path(raw))
            fixture["inventory"][0]["transcript_sha256"] = "f" * 64
            with self.assertRaisesRegex(SMR.RecoveryError, "binding is stale"):
                SMR.validate_cnn_local_reuse_inventory_binding(
                    rows, fixture["inventory"], inventory_file_sha256="1" * 64
                )

    def test_concurrent_memo_single_flights_success_and_retries_failures(self):
        memo = SMR.ConcurrentMemo(4)
        calls = 0
        lock = threading.Lock()

        def compute():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.02)
            return {"immutable": True}

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(
                lambda _index: memo.get_or_compute("same", compute), range(8)
            ))
        self.assertEqual(values, [{"immutable": True}] * 8)
        self.assertEqual(calls, 1)

        attempts = 0

        def flaky():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SMR.RecoveryError("temporary")
            return "recovered"

        with self.assertRaises(SMR.RecoveryError):
            memo.get_or_compute("flaky", flaky)
        self.assertEqual(memo.get_or_compute("flaky", flaky), "recovered")
        self.assertEqual(attempts, 2)

    def test_bounded_executor_processes_complete_input(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            rows = list(SMR.bounded_executor_results(
                executor, range(31), lambda value: value * value, max_in_flight=5
            ))
        self.assertEqual(sorted(rows), [(value, value * value) for value in range(31)])

    def test_cnn_archive_memo_reuses_exact_broadcast_fetches(self):
        records = [{
            "package": "https://transcripts.cnn.com/show/ctmo/date/2026-08-28/segment/%02d" % segment,
            "date": "2026-08-28",
        } for segment in (1, 2)]
        identifier = "CNNW_20260828_100000_CNN_This_Morning"
        shared = SMR.shared_discovery_state(set())
        with mock.patch.object(SMR, "fetch_text", return_value="page") as fetch, \
                mock.patch.object(
                    SMR, "cnn_airtime",
                    return_value=dt.datetime(2026, 8, 28, 10, 30, tzinfo=dt.timezone.utc),
                ), \
                mock.patch.object(SMR, "archive_search_prefix", return_value=[{
                    "identifier": identifier, "title": "CNN This Morning",
                }]) as search, \
                mock.patch.object(SMR, "caption_spans_for_record", return_value=[]), \
                mock.patch.object(SMR, "archive_media_metadata", return_value={
                    "metadata": {}, "file": {
                        "name": identifier + ".mp4", "size": "100",
                        "md5": "m", "sha1": "s", "private": "false",
                    },
                }) as metadata:
            results = [SMR.discover_cnn_archive(record, shared) for record in records]
        self.assertEqual([row["status"] for row in results],
                         ["candidate_asr_required", "candidate_asr_required"])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(metadata.call_count, 1)
        # Two distinct transcript pages plus one shared archive item page.
        self.assertEqual(fetch.call_count, 3)

    def test_source_family_routes_collected_hosts(self):
        cases = {
            "https://transcripts.cnn.com/show/foo/date/2026-01-01/segment/01": "cnn",
            "https://www.cnn.com/audio/podcasts/axe-files/episodes/abc-def": "cnn-axe-files",
            "https://www.foxnews.com/transcript/example": "fox",
            "https://www.ms.now/transcripts/example": "msnow",
            "https://www.cbsnews.com/news/face-the-nation-full-transcript/": "cbs-ftn",
            "https://www.pbs.org/newshour/show/example": "pbs",
            "https://www.npr.org/transcripts/123": "npr",
            "https://www.csis.org/analysis/example": "csis",
        }
        self.assertEqual(
            {url: SMR.source_family(url) for url in cases}, cases
        )

    def test_inventory_keeps_exact_rowids_and_hashes(self):
        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "speech.db"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,"
                "package TEXT,nwords INTEGER,text TEXT)"
            )
            package = "https://transcripts.cnn.com/show/cnr/date/2026-01-02/segment/01"
            con.executemany(
                "INSERT INTO speeches VALUES (?,?,?,?,?,?,?)",
                [
                    ("Ada Lovelace", "2026-01-02", "INTERVIEW", "cnr", package, 13, "one two three four five six seven eight nine ten eleven twelve thirteen"),
                    ("Ada Lovelace", "2026-01-02", "INTERVIEW", "cnr", package, 12, "another exact stored transcript turn with enough words for a safe alignment test later"),
                    ("Ada Lovelace", "2026-01-02", "OTHER", "cnr", package, 99, "must be excluded"),
                ],
            )
            con.commit()
            con.close()
            with mock.patch.object(SMR, "load_existing_prepared", return_value=(set(), set(), set())):
                rows = SMR.snapshot_inventory(db)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_family"], "cnn")
        self.assertEqual(row["turn_count"], 2)
        self.assertEqual(row["members"][0]["rowids"], [1, 2])
        self.assertEqual(row["word_count"], 25)
        self.assertEqual(row["discovery"]["status"], "needs_probe")
        self.assertEqual(len(row["transcript_sha256"]), 64)

    def test_ordered_alignment_is_monotonic_and_order_sensitive(self):
        transcript = SMR.normalize_tokens(
            "we need a durable budget agreement that protects families and keeps the government open"
        )
        asr = SMR.normalize_tokens(
            "host introduction unrelated words and now we need a durable budget agreement that protects "
            "families and keeps the government open thank you"
        )
        match = SMR.ordered_alignment(transcript, asr, 0, minimum_ratio=0.8)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(match[0], 4)
        self.assertGreater(match[1], match[0])
        self.assertGreaterEqual(match[2], 0.8)
        self.assertIsNone(
            SMR.ordered_alignment(transcript, list(reversed(asr)), 0, minimum_ratio=0.8)
        )

    def test_fox_feed_requires_unique_exact_show_date(self):
        record = {
            "date": "2026-08-23",
            "package": "https://www.foxnews.com/transcript/fox-news-sunday-8-23",
        }
        item = {
            "date": "2026-08-23",
            "title": "FOX News Sunday",
            "guid": "abc-123",
            "media_url": "https://traffic.megaphone.fm/example.mp3",
        }
        result = SMR.discover_fox_sunday(record, {"2026-08-23": [item]})
        self.assertEqual(result["status"], "exact_episode_asr_required")
        self.assertEqual(result["media_id"], "fox-megaphone:abc-123")
        ambiguous = SMR.discover_fox_sunday(record, {"2026-08-23": [item, dict(item)]})
        self.assertEqual(ambiguous["status"], "ambiguous")

    def test_abc_feed_requires_unique_exact_full_episode_date(self):
        record = {"date": "2024-10-13"}
        item = {
            "guid": "ESP5690711434",
            "title": "Full Episode: Sunday, October 13, 2024",
            "date": "2024-10-13",
            "media_url": "https://traffic.megaphone.fm/ESP5690711434.mp3",
        }
        result = SMR.discover_abc_this_week(record, {"2024-10-13": [item]})
        self.assertEqual(result["status"], "exact_episode_asr_required")
        self.assertEqual(result["provider"], "abc-megaphone")
        self.assertEqual(result["evidence"]["feed_date"], "2024-10-13")
        ambiguous = SMR.discover_abc_this_week(
            record, {"2024-10-13": [item, dict(item)]}
        )
        self.assertEqual(ambiguous["status"], "ambiguous")

    def test_abc_inline_segments_filter_date_program_and_mirrors(self):
        record = {
            "date": "2020-01-19",
            "package": "https://abcnews.go.com/Politics/week-transcript/story?id=1",
        }
        page = " ".join([
            "https://service-pkgabcnews.akamaized.net/opp/hls/abcnews/2020/01/200119_tw_booker1_1028_,500,800,.mp4.csmil/playlist.m3u8",
            "https://cmp-abcnews.media.dssott.com/opp/cmaf/abcnews/2020/01/200119_tw_booker1_1028_,500,800,.mp4.csmil/playlist.m3u8",
            "https://cmp-abcnews.media.dssott.com/opp/cmaf/abcnews/2020/01/200118_wn_wright_739_,500,800,.mp4.csmil/playlist.m3u8",
        ])
        result = SMR.discover_abc_inline_segments(record, page)
        self.assertEqual(result["status"], "candidate_asr_required")
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["provider"], "abc-official-video")
        self.assertEqual(candidate["evidence"]["segment_key"], "200119_tw_booker1_1028")
        self.assertIn("cmp-abcnews.media.dssott.com", candidate["media_url"])

    def test_cnn_search_query_uses_show_date_member_and_text(self):
        record = {
            "package": "https://transcripts.cnn.com/show/sotu/date/2026-08-23/segment/01",
            "date": "2026-08-23",
            "show": "sotu",
        }
        query = SMR.cnn_search_query(
            record,
            "Jane Example",
            "Distinctive bipartisan appropriations language appears in this interview passage.",
        )
        self.assertIn("sotu", query)
        self.assertIn("2026-08-23", query)
        self.assertIn("Jane Example", query)
        self.assertIn("distinctive", query)

    def test_tv_caption_query_uses_five_distinctive_words(self):
        phrase = SMR.distinctive_five_word_phrase(
            "It is a challenge for us on the campaign to connect and communicate with people "
            "about whether families are better off now than four years ago."
        )
        self.assertEqual(len(phrase.split()), 5)
        self.assertIn("communicate", phrase)

    def test_current_archive_tv_search_filters_channel_and_local_or_next_utc_day(self):
        record = {"date": "2015-06-22"}
        turns = [{
            "rowid": 1,
            "text": "students can actually hold them accountable when schools fail to report these crimes",
        }]
        hits = [
            {
                "hit_type": "tv_clip",
                "fields": {
                    "identifier": "MSNBCW_20150623_060000_Hardball_With_Chris_Matthews",
                    "title": "Hardball : MSNBCW : June 22, 2015 11:00pm PDT",
                    "date": "2015-06-23T00:00:00Z",
                    "start": "1767",
                    "__href__": "/details/item/start/1767/end/1827",
                },
                "highlight": {"text": ["actually hold them accountable when"]},
            },
            {
                "hit_type": "tv_clip",
                "fields": {"identifier": "FOXNEWSW_20150623_000000_Other"},
            },
            {
                "hit_type": "tv_clip",
                "fields": {"identifier": "MSNBCW_20150625_000000_Too_Late"},
            },
        ]
        payload = {"response": {"body": {"hits": {"hits": hits}}}}
        with mock.patch.object(SMR, "load_turns_for_record", return_value=turns), \
                mock.patch.object(SMR, "fetch_json", return_value=payload):
            phrase, clips = SMR.archive_tv_clips(record, "MSNBCW")
        self.assertIn("accountable", phrase)
        self.assertEqual(len(clips), 1)
        self.assertEqual(
            clips[0]["ia_show_id"],
            "MSNBCW_20150623_060000_Hardball_With_Chris_Matthews",
        )

    def test_distant_caption_turns_are_split_into_bounded_groups(self):
        discovery = {"evidence": {"caption_spans": [
            {"rowid": 1, "start_sec": 100.0, "end_sec": 115.0},
            {"rowid": 2, "start_sec": 130.0, "end_sec": 145.0},
            {"rowid": 3, "start_sec": 500.0, "end_sec": 515.0},
        ]}}
        groups = SMR.caption_span_groups(discovery, maximum_window_sec=90.0)
        self.assertEqual([[row["rowid"] for row in group] for group in groups], [[1, 2], [3]])

    def test_alignment_runner_isolates_bad_acquisition_subspans(self):
        source = Path(SMR.__file__).read_text(encoding="utf-8")
        self.assertIn("item_errors.append(failure)", source)
        self.assertIn('status = "candidate_clips_emitted"', source)
        self.assertIn('elif item_errors:', source)
        self.assertIn("checkpoint_current", source)
        self.assertIn("zero_candidate_current", source)
        self.assertIn('"no_speech_or_insufficient_asr"', source)
        self.assertIn("terminal unresolved", source)
        self.assertIn('alignment.get("status") == "no_member_turn_matched"', source)
        self.assertNotIn("if marker_current or checkpoint_current:", source)
        self.assertIn("stale_clip_ids", source)
        self.assertIn('"clip_schema_counts"', source)

    def test_parallel_runners_checkpoint_completion_order(self):
        source = Path(SMR.__file__).read_text(encoding="utf-8")
        self.assertIn("concurrent.futures.as_completed", source)
        self.assertNotIn("executor.map(", source)
        self.assertIn("with source_lock(acquisition):", source)
        self.assertIn("racing duplicate writers", source)

    def test_atomic_replace_retries_short_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw) / "value.tmp"
            target = Path(raw) / "value.json"
            temp.write_text("ok", encoding="utf-8")
            real_replace = SMR.os.replace
            attempts = {"count": 0}

            def flaky_replace(source, destination):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise PermissionError("temporary scanner lock")
                return real_replace(source, destination)

            with mock.patch.object(SMR.os, "replace", side_effect=flaky_replace), \
                    mock.patch.object(SMR.time, "sleep"):
                SMR.replace_atomic_with_retry(temp, target, attempts=3)
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")
            self.assertEqual(attempts["count"], 3)

    def test_staged_clip_key_changes_for_non_millisecond_frame_refresh(self):
        common = ("a" * 64, 48000, 16000, "sqlite:speeches:9", "b" * 64)
        first = SMR.staged_clip_key(common[0], 1, common[1], common[2], common[3], common[4])
        refreshed = SMR.staged_clip_key(common[0], 2, common[1], common[2], common[3], common[4])
        self.assertNotEqual(first, refreshed)

    def test_fox_official_inline_media_short_circuits_archive_search(self):
        record = {
            "source_family": "fox",
            "date": "2023-04-09",
            "show": "Fox News Transcript",
            "package": "https://www.foxnews.com/transcript/example",
        }
        page = (
            'accountId: "694940094001", videoId: "6312345678901", '
            'https://vod.foxnews.com/example.mp4'
        )
        with mock.patch.object(SMR, "fetch_text", return_value=page), \
                mock.patch.object(SMR, "discover_tvnews_phrase") as archive:
            result = SMR.discover_one(record, {"fox_by_date": {}})
        self.assertEqual(result["status"], "partial_clip_asr_required")
        self.assertEqual(result["provider"], "fox-brightcove")
        archive.assert_not_called()

    def test_fox_archive_asr_candidate_is_not_discarded(self):
        package = "https://www.foxnews.com/transcript/example"
        record = {
            "source_family": "fox",
            "date": "2023-04-09",
            "show": "Fox News Sunday",
            "package": package,
            "package_id": SMR.package_id(package),
        }
        candidate = SMR.media_result(
            "candidate_asr_required",
            "fox_archive+local_whisper_required",
            "exact broadcast candidate requires local ASR",
            provider="internet-archive-tvnews",
            media_id="ia:FOXNEWSW_20230409_140000_Fox_News_Sunday",
            media_url="https://archive.org/download/example/example.mp4",
            evidence={
                "identifier": "FOXNEWSW_20230409_140000_Fox_News_Sunday",
                "utc_airtime": "2023-04-09T14:00:00+00:00",
                "broadcast_title": "Fox News Sunday : April 9, 2023",
            },
        )
        with mock.patch.object(SMR, "fetch_text", return_value="no media"), \
                mock.patch.object(
                    SMR, "discover_tvnews_program",
                    return_value=SMR.media_result("no_media", "test", "none"),
                ), \
                mock.patch.object(SMR, "discover_tvnews_phrase", return_value=candidate), \
                mock.patch.object(
                    SMR, "discover_fox_sunday",
                    return_value=SMR.media_result("no_media", "test", "none"),
                ):
            result = SMR.discover_one(record, {"fox_by_date": {}})
        self.assertEqual(result["status"], "candidate_asr_required")
        self.assertEqual(result["media_id"], candidate["media_id"])

    def test_fox_exact_program_asr_candidate_is_not_discarded(self):
        package = "https://www.foxnews.com/transcript/example"
        record = {
            "source_family": "fox",
            "date": "2023-04-09",
            "show": "Fox News Sunday",
            "package": package,
            "package_id": SMR.package_id(package),
        }
        candidate = SMR.media_result(
            "candidate_asr_required",
            "fox_program+local_whisper_required",
            "exact program requires ASR",
            provider="internet-archive-tvnews",
            media_id="ia:FOXNEWSW_20230409_140000_Fox_News_Sunday",
            media_url="https://archive.org/download/example/example.mp4",
            evidence={"source_private": False},
        )
        with mock.patch.object(SMR, "fetch_text", return_value="no media"), \
                mock.patch.object(SMR, "discover_tvnews_program", return_value=candidate), \
                mock.patch.object(SMR, "discover_tvnews_phrase") as phrase:
            result = SMR.discover_one(record, {"fox_by_date": {}})
        self.assertEqual(result, candidate)
        phrase.assert_not_called()

    def test_cnn_official_fallback_candidates_reach_acquisition(self):
        record = {
            "source_family": "cnn",
            "date": "2026-08-28",
            "package": "https://transcripts.cnn.com/show/test/date/2026-08-28/segment/01",
        }
        fallback = {
            "status": "candidate_asr_required",
            "provider": "youtube",
            "media_id": "youtube:abcdefghijk",
            "media_url": "https://www.youtube.com/watch?v=abcdefghijk",
        }
        with mock.patch.object(
            SMR, "discover_cnn_archive",
            return_value=SMR.media_result("no_media", "cnn", "none"),
        ), mock.patch.object(SMR, "official_cnn_search_candidates", return_value=[fallback]):
            discovered = SMR.discover_one(record, {}, cnn_search=True)
        self.assertEqual(discovered["status"], "candidate_asr_required")
        self.assertEqual(discovered["candidates"], [fallback])
        row = {"discovery": discovered}
        self.assertEqual(SMR.candidate_discoveries(row), [fallback])

    def test_private_cnn_archive_candidate_does_not_suppress_official_fallback(self):
        record = {
            "source_family": "cnn",
            "date": "2026-08-28",
            "package": "https://transcripts.cnn.com/show/test/date/2026-08-28/segment/01",
        }
        private = SMR.media_result(
            "candidate_asr_required",
            "cnn-archive",
            "private archive source",
            provider="internet-archive-tvnews",
            media_id="ia:CNNW_private",
            media_url="https://archive.org/download/private/private.mp4",
            evidence={"source_private": True},
        )
        fallback = {
            "status": "candidate_asr_required",
            "provider": "youtube",
            "media_id": "youtube:abcdefghijk",
            "media_url": "https://www.youtube.com/watch?v=abcdefghijk",
        }
        with mock.patch.object(SMR, "discover_cnn_archive", return_value=private), \
                mock.patch.object(SMR, "official_cnn_search_candidates", return_value=[fallback]):
            discovered = SMR.discover_one(record, {}, cnn_search=True)
        self.assertEqual(discovered["candidates"], [fallback])
        self.assertEqual(discovered["status"], "candidate_asr_required")
        persisted_row = {"discovery": discovered}
        self.assertFalse(SMR.normalize_private_discovery(persisted_row))
        self.assertEqual(persisted_row["discovery"]["status"], "candidate_asr_required")
        self.assertEqual(SMR.candidate_discoveries({"discovery": discovered}), [fallback])

        # A private primary is never acquirable, including when no fallback was
        # found.  It remains unresolved instead of leaking into acquisition.
        private_only = SMR.media_result(
            "candidate_asr_required",
            "cnn-archive",
            "private archive source",
            provider="internet-archive-tvnews",
            media_id="ia:CNNW_private_only",
            media_url="https://archive.org/download/private/private.mp4",
            evidence={"source_private": True},
        )
        self.assertEqual(SMR.candidate_discoveries({"discovery": private_only}), [])
        private_fallback = {
            "status": "candidate_asr_required",
            "provider": "internet-archive-tvnews",
            "media_id": "ia:CNNW_private_fallback",
            "media_url": "https://archive.org/download/private/fallback.mp4",
            "evidence": {"source_private": "true"},
        }
        self.assertEqual(
            SMR.candidate_discoveries({
                "discovery": SMR.media_result(
                    "candidate_asr_required", "cnn", "fallbacks", candidates=[private_fallback]
                )
            }),
            [],
        )
        private_inventory_row = {"discovery": dict(private_only)}
        self.assertTrue(SMR.normalize_private_discovery(private_inventory_row))
        self.assertEqual(
            private_inventory_row["discovery"]["status"],
            "blocked_no_public_media_id",
        )
        self.assertEqual(
            private_inventory_row["discovery"]["evidence"]["private_source_original_status"],
            "candidate_asr_required",
        )
        private["status"] = "exact_timed_captions"
        with mock.patch.object(SMR, "discover_cnn_archive", return_value=private), \
                mock.patch.object(SMR, "official_cnn_search_candidates", return_value=[fallback]):
            discovered = SMR.discover_one(record, {}, cnn_search=True)
        self.assertEqual(discovered["candidates"], [fallback])
        self.assertEqual(SMR.candidate_discoveries({"discovery": discovered}), [fallback])

    def test_cnn_missing_airtime_does_not_block_candidate_specific_media(self):
        record = {
            "source_family": "cnn",
            "date": "2026-08-28",
            "package": "https://transcripts.cnn.com/show/test/date/2026-08-28/segment/01",
        }
        fallback = {
            "status": "candidate_asr_required",
            "provider": "youtube",
            "media_id": "youtube:abcdefghijk",
            "media_url": "https://www.youtube.com/watch?v=abcdefghijk",
        }
        with mock.patch.object(
            SMR, "discover_cnn_archive", side_effect=SMR.RecoveryError("explicit airtime absent")
        ), mock.patch.object(
            SMR, "official_cnn_search_candidates", return_value=[fallback]
        ):
            discovered = SMR.discover_one(record, {}, cnn_search=True)
        self.assertEqual(discovered["status"], "candidate_asr_required")
        self.assertEqual(discovered["candidates"], [fallback])
        self.assertIn(
            "archive_error",
            discovered["evidence"]["archive_accelerator_evidence"],
        )
        self.assertEqual(SMR.candidate_discoveries({"discovery": discovered}), [fallback])

    def test_real_cnn_official_search_treats_airtime_as_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "speech.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE speeches (member TEXT, text TEXT, source TEXT)"
                )
                connection.execute(
                    "INSERT INTO speeches(rowid, member, text, source) VALUES (1, ?, ?, 'INTERVIEW')",
                    ("Member One", "A distinctive stored transcript phrase for alignment"),
                )
                connection.commit()
            finally:
                connection.close()
            record = {
                "source_family": "cnn",
                "date": "2026-08-28",
                "show": "test",
                "package": "https://transcripts.cnn.com/show/test/date/2026-08-28/segment/01",
                "package_id": "pkg:test",
                "members": [{"member": "Member One", "rowids": [1]}],
            }
            search_payload = {
                "entries": [{
                    "id": "abcdefghijk",
                    "channel_id": next(iter(SMR.OFFICIAL_YOUTUBE_CHANNELS["cnn"])),
                    "title": "Candidate interview",
                }]
            }
            process = mock.Mock(
                returncode=0,
                stdout=json.dumps(search_payload),
                stderr="",
            )
            with mock.patch.object(
                SMR, "fetch_text", return_value="<html>no aired clock here</html>"
            ), mock.patch.object(SMR.subprocess, "run", return_value=process):
                candidates = SMR.official_cnn_search_candidates(record, database)
        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0]["evidence"]["transcript_airtime_utc"])
        self.assertEqual(candidates[0]["media_id"], "youtube:abcdefghijk")

    def test_cnn_airtime_and_archive_caption_minutes(self):
        record = {"date": "2026-08-28"}
        aired = SMR.cnn_airtime(
            record,
            "<html><body>Aired August 28, 2026 - 06:30 ET</body></html>",
        )
        self.assertEqual(aired, dt.datetime(2026, 8, 28, 10, 30, tzinfo=dt.timezone.utc))
        candidate_wrong_date = SMR.cnn_explicit_airtime(
            "<html><body>Aired August 27, 2026 - 06:30 ET</body></html>"
        )
        self.assertEqual(
            candidate_wrong_date,
            dt.datetime(2026, 8, 27, 10, 30, tzinfo=dt.timezone.utc),
        )
        with self.assertRaisesRegex(SMR.RecoveryError, "differs"):
            SMR.cnn_airtime(
                record,
                "<html><body>Aired August 27, 2026 - 06:30 ET</body></html>",
            )
        page = """
        <div class="tvcol" data-idx="34">
          <a href="/start/2040/end/2100"></a>
          <div class="snipin nosel">We want America run by American patriots.</div>
        </div>
        <div class="tvcol" data-idx="35">
          <a href="/start/2100/end/2160"></a>
          <div class="snipin nosel">Members of Congress and judges should be citizens.</div>
        </div>
        """
        tokens, times = SMR.archive_caption_minutes(page)
        self.assertEqual(tokens[:3], ["we", "want", "america"])
        self.assertEqual(times[0][0], 2040.0)
        self.assertGreater(times[-1][1], 2100.0)

    def test_cnn_archive_resolver_returns_complete_exact_binding(self):
        record = {
            "package": "https://transcripts.cnn.com/show/ctmo/date/2026-08-28/segment/02",
            "date": "2026-08-28",
        }
        span = [{
            "rowid": 9,
            "member": "Nancy Mace",
            "start_sec": 10.0,
            "end_sec": 20.0,
            "caption_alignment_ratio": 0.95,
        }]
        with mock.patch.object(SMR, "fetch_text", return_value="page"), \
                mock.patch.object(
                    SMR, "cnn_airtime",
                    return_value=dt.datetime(2026, 8, 28, 10, 30, tzinfo=dt.timezone.utc),
                ), \
                mock.patch.object(SMR, "archive_search_prefix", return_value=[{
                    "identifier": "CNNW_20260828_100000_CNN_This_Morning",
                    "title": "CNN This Morning",
                }]), \
                mock.patch.object(SMR, "caption_spans_for_record", return_value=span), \
                mock.patch.object(SMR, "archive_media_metadata", return_value={
                    "metadata": {},
                    "file": {
                        "name": "CNNW_20260828_100000_CNN_This_Morning.mp4",
                        "size": "100",
                        "md5": "m",
                        "sha1": "s",
                        "private": "true",
                    },
                }):
            result = SMR.discover_cnn_archive(record)
        self.assertEqual(result["status"], "exact_timed_captions")
        self.assertEqual(result["media_id"], "ia:CNNW_20260828_100000_CNN_This_Morning")
        self.assertEqual(result["evidence"]["caption_spans"], span)

    def test_cnn_archive_without_captions_is_still_acquirable_for_local_asr(self):
        record = {
            "package": "https://transcripts.cnn.com/show/ctmo/date/2026-08-28/segment/02",
            "date": "2026-08-28",
        }
        with mock.patch.object(SMR, "fetch_text", return_value="page"), \
                mock.patch.object(
                    SMR, "cnn_airtime",
                    return_value=dt.datetime(2026, 8, 28, 10, 30, tzinfo=dt.timezone.utc),
                ), \
                mock.patch.object(SMR, "archive_search_prefix", return_value=[{
                    "identifier": "CNNW_20260828_100000_CNN_This_Morning",
                    "title": "CNN This Morning : August 28, 2026",
                }]), \
                mock.patch.object(SMR, "caption_spans_for_record", return_value=[]), \
                mock.patch.object(SMR, "archive_media_metadata", return_value={
                    "metadata": {},
                    "file": {
                        "name": "CNNW_20260828_100000_CNN_This_Morning.mp4",
                        "size": "100",
                        "md5": "m",
                        "sha1": "s",
                        "private": "false",
                    },
                }):
            result = SMR.discover_cnn_archive(record)
        self.assertEqual(result["status"], "candidate_asr_required")
        self.assertTrue(result["media_url"].startswith("https://archive.org/download/"))
        self.assertEqual(result["evidence"]["access"], "public-full-program-asr")
        self.assertEqual(result["evidence"]["caption_spans"], [])

    def test_abc_exact_program_date_without_captions_advances_only_to_asr(self):
        record = {
            "package": "https://abcnews.go.com/Politics/week-transcript/story?id=1",
            "date": "2023-01-15",
            "show": "this-week",
        }
        identifier = "KGO_20230115_160000_This_Week_With_George_Stephanopoulos"
        with mock.patch.object(SMR, "archive_search_pattern", return_value=[{
                "identifier": identifier,
                "title": "This Week With George Stephanopoulos : KGO : January 15, 2023",
             }]), \
                mock.patch.object(SMR, "fetch_text", return_value="page"), \
                mock.patch.object(SMR, "caption_spans_for_record", return_value=[]), \
                mock.patch.object(SMR, "archive_media_metadata", return_value={
                    "metadata": {},
                    "file": {"name": identifier + ".mp4", "size": "100", "md5": "m", "sha1": "s"},
                }):
            result = SMR.discover_one(record | {"source_family": "abc-this-week"}, {})
        self.assertEqual(result["status"], "candidate_asr_required")
        self.assertEqual(result["evidence"]["access"], "public-full-program-asr")
        self.assertEqual(result["evidence"]["utc_airtime"], "2023-01-15T16:00:00+00:00")

    def test_unique_tv_phrase_hit_without_full_caption_binding_advances_to_whisper(self):
        record = {"date": "2015-06-22", "package": "https://www.ms.now/transcript/example"}
        identifier = "MSNBCW_20150623_060000_Hardball_With_Chris_Matthews"
        clips = [{
            "ia_show_id": identifier,
            "show": "Hardball : MSNBCW : June 22, 2015",
            "start": "1767",
            "preview_url": "https://archive.org/details/example/start/1767/end/1827",
            "snippet": "distinctive phrase",
            "search_backend": "archive-org-tvs-page-production",
        }]
        with mock.patch.object(SMR, "archive_tv_clips", return_value=("distinctive phrase", clips)), \
                mock.patch.object(SMR, "fetch_text", return_value="page"), \
                mock.patch.object(SMR, "caption_spans_for_record", return_value=[]), \
                mock.patch.object(SMR, "archive_media_metadata", return_value={
                    "metadata": {},
                    "file": {"name": identifier + ".mp4", "size": "100", "md5": "m", "sha1": "s"},
                }):
            result = SMR.discover_tvnews_phrase(record, "MSNBC", "MSNBCW_", "adapter")
        self.assertEqual(result["status"], "candidate_asr_required")
        self.assertEqual(result["evidence"]["asr_hint_start_sec"], 1767.0)
        self.assertEqual(result["evidence"]["access"], "public-search-hit-bounded-asr")

    def test_summary_does_not_call_blocked_sources_pending(self):
        rows = [
            {
                "source_family": "cnn",
                "turn_count": 2,
                "word_count": 20,
                "discovery": {"status": "blocked_no_public_media_id"},
            },
            {
                "source_family": "pbs",
                "turn_count": 3,
                "word_count": 30,
                "discovery": {"status": "needs_probe"},
            },
        ]
        summary = SMR.summarize(rows)
        self.assertFalse(summary["all_packages_dispositioned"])
        self.assertEqual(summary["pending_packages"], 1)
        rows[1]["discovery"]["status"] = "no_media"
        self.assertTrue(SMR.summarize(rows)["all_packages_dispositioned"])

    def test_custom_inventory_uses_isolated_sidecars(self):
        with tempfile.TemporaryDirectory() as raw:
            inventory = Path(raw) / "abc_snapshot.jsonl"
            summary, receipt = SMR.inventory_sidecars(inventory)
            self.assertEqual(summary, Path(raw) / "abc_snapshot.summary.json")
            self.assertEqual(receipt, Path(raw) / "abc_snapshot.receipt.json")
            self.assertNotEqual(summary.resolve(), SMR.SUMMARY_PATH.resolve())
            self.assertNotEqual(receipt.resolve(), SMR.RECEIPT_PATH.resolve())

            rows = [{
                "package": "https://example.test/transcript",
                "source_family": "abc-this-week",
                "turn_count": 1,
                "word_count": 12,
                "discovery": {"status": "no_media"},
            }]
            result = SMR.persist(rows, inventory)
            self.assertTrue(result["all_packages_dispositioned"])
            self.assertTrue(inventory.is_file())
            self.assertTrue(summary.is_file())
            self.assertTrue(receipt.is_file())
            written = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(Path(written["inventory_path"]), inventory.resolve())
            self.assertEqual(Path(written["summary_path"]), summary.resolve())

    def test_legacy_audit_is_provenance_only_and_non_promoting(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            db = root / "speech.db"
            wav = root / "turn.wav"
            package = "https://transcripts.cnn.com/show/ctmo/date/2026-08-28/segment/02"
            text = "we need an exact stored transcript turn with enough distinctive words to audit safely"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,"
                "package TEXT,nwords INTEGER,text TEXT)"
            )
            con.execute(
                "INSERT INTO speeches VALUES (?,?,?,?,?,?,?)",
                ("Nancy Mace", "2026-08-28", "INTERVIEW", "ctmo", package, 13, text),
            )
            con.commit()
            con.close()
            with wave.open(str(wav), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\0\0" * 32000)
            record = {
                "package": package,
                "package_id": SMR.package_id(package),
                "source_family": "cnn",
                "date": "2026-08-28",
                "discovery": {
                    "status": "exact_timed_captions",
                    "provider": "internet-archive-tvnews",
                    "media_id": "ia:CNNW_20260828_100000_CNN_This_Morning",
                    "media_url": "https://archive.org/download/example/example.mp4",
                    "adapter": "cnn_tvnews_airtime+public_caption_alignment",
                    "evidence": {
                        "identifier": "UNRELATED_PROGRAM_IDENTIFIER",
                        "utc_airtime": "1999-01-01T00:00:00+00:00",
                        "broadcast_title": "Different program and date",
                        "caption_spans": [],
                    },
                },
            }
            candidate = {
                "schema": SMR.CLIP_SCHEMA,
                "status": "CANDIDATE_AUDIT_REQUIRED",
                "clip_id": "source-media:test:1",
                "member": "Nancy Mace",
                "source_family": "cnn",
                "show": "ctmo",
                "date": "2026-08-28",
                "package": package,
                "turn_id": "sqlite:speeches:1",
                "transcript_path": str(db.resolve()) + "#speeches.rowid=1",
                "text_sha256": SMR.sha256_text(text),
                "source_audio_sha256": "a" * 64,
                "provider": "internet-archive-tvnews",
                "media_id": "ia:CNNW_20260828_100000_CNN_This_Morning",
                "binding_method": "official-media-id+whisper-order-sensitive-monotonic-transcript-alignment",
                "alignment_ratio": 0.93,
                "start_sec": 100.0,
                "end_sec": 102.0,
                "duration_sec": 2.0,
                "words": 13,
                "wav": str(wav.resolve()),
                "audio_sha256": SMR.sha256_file(wav),
                "sample_rate": 16000,
                "features": {name: 0.5 for name in SMR.REQUIRED_AUDIT_FEATURES},
            }
            candidate["clip_sha256"] = SMR._candidate_hash(candidate)
            accepted = SMR.audit_candidate(
                candidate,
                db_path=db,
                roster={"Nancy Mace"},
                inventory_by_package={package: record},
                minimum_ratio=0.68,
            )
            self.assertEqual(
                accepted["status"],
                "PROVENANCE_VERIFIED_SUPPLEMENTAL_DISPLAY_ONLY_NO_IDENTITY",
            )
            self.assertTrue(accepted["audit"]["candidate_provenance_pass"])
            self.assertFalse(accepted["audit"]["program_airtime_used_for_identity"])
            self.assertFalse(accepted["audit"]["identity_accepted"])
            self.assertFalse(accepted["audit"]["promotable"])
            broken = json.loads(json.dumps(record))
            broken["discovery"]["media_id"] = "ia:not-the-candidate"
            with self.assertRaisesRegex(SMR.RecoveryError, "provenance"):
                SMR.audit_candidate(
                    candidate,
                    db_path=db,
                    roster={"Nancy Mace"},
                    inventory_by_package={package: broken},
                    minimum_ratio=0.68,
                )


if __name__ == "__main__":
    unittest.main()
