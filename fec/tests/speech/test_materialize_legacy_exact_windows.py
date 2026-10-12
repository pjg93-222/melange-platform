import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from speech import materialize_legacy_exact_windows as M  # noqa: E402


def write_wav(path: Path, frames: int, seed: int) -> str:
    pcm = bytes(((index + seed) % 251 for index in range(frames * 2)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    ), encoding="utf-8")


class ExactLegacyWindowTests(unittest.TestCase):
    def fixture(self, root: Path):
        nonfloor = root / "nonfloor.wav"
        floor = root / "floor.wav"
        superseded = root / "superseded.wav"
        nonfloor_sha = write_wav(nonfloor, M.WINDOW_FRAMES * 2 + 17, 1)
        floor_sha = write_wav(floor, M.WINDOW_FRAMES, 2)
        superseded_sha = write_wav(superseded, M.WINDOW_FRAMES, 3)
        rows = [
            {
                "member": "Member One", "outlet": "NPR", "show": "NPR",
                "out_wav": str(nonfloor), "src_wav": str(root / "source.wav"),
                "start": 4.0, "frames": M.WINDOW_FRAMES * 2 + 17,
                "samplerate": 16000, "sha256": nonfloor_sha,
            },
            {
                "member": "Member Floor", "outlet": "HouseLive", "show": "Floor",
                "out_wav": str(floor), "src_wav": str(floor), "start": 0.0,
                "frames": M.WINDOW_FRAMES, "samplerate": 16000, "sha256": floor_sha,
            },
            {
                "member": "Member Old", "outlet": "SUNDAY", "show": "Old",
                "out_wav": str(superseded), "src_wav": str(superseded), "start": 0.0,
                "frames": M.WINDOW_FRAMES, "samplerate": 16000,
                "sha256": superseded_sha,
            },
        ]
        manifest = root / "manifest.jsonl"
        write_jsonl(manifest, rows)
        manifest_sha = M.sha256_file(manifest)
        old_id = M.row_sha256(rows[2])
        supersession = [{
            "action": "CANONICAL_RECORD_SUPERSEDED_SOURCE_INTERVAL_OVERLAP",
            "canonical_manifest_row_sha256": old_id,
            "canonical_clip_audio_sha256": superseded_sha,
            "member": "Member Old", "out_wav": str(superseded),
            "superseded_by": [{"clip_id": "verified:new", "overlap_frames": 128000}],
        }]
        artifact = {
            "schema": "speech-descriptive-v1",
            "contract": {
                "canonical_source_interval_supersession_required": True,
                "floor_audio_in_headline": False,
            },
            "source": {
                "manifest_sha256": manifest_sha,
                "canonical_supersession": {
                    "schema": "speech-canonical-record-supersession-v1",
                    "physical_cleanup_authorized": False,
                    "superseded_rows": 1,
                    "supersession_rows_sha256": M.stable_sha256(supersession),
                    "records": supersession,
                },
            },
        }
        artifact_path = root / "artifact.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return manifest, artifact_path

    def run_fixture(self, root: Path, mode="account"):
        manifest, artifact = self.fixture(root)
        kwargs = {}
        if mode == "materialize":
            kwargs["audio_dir"] = root / "windows"
        return M.process(
            manifest, artifact, root / "windows.jsonl", root / "excluded.jsonl",
            root / "receipt.json", mode=mode, **kwargs,
        )

    def test_accounts_every_active_nonfloor_frame_and_excludes_floor_and_superseded(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, exclusions, receipt = self.run_fixture(Path(directory))
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row["pcm_sha256"] for row in rows}), 2)
            self.assertEqual(len({row["lineage"]["source_frame_identity_sha256"]
                                  for row in rows}), 2)
            self.assertTrue(all(row["frames"] == 128000 for row in rows))
            self.assertTrue(all(row["setting"] == "non_house_floor" for row in rows))
            starts = sorted(row["lineage"]["source_start_frame"] for row in rows)
            self.assertEqual(starts[1] - starts[0], M.WINDOW_FRAMES)
            self.assertEqual(
                {row["reason"] for row in exclusions},
                {"HOUSE_FLOOR_EXCLUDED_FROM_HEADLINE",
                 "TRAILING_FRAGMENT_SHORTER_THAN_EXACT_WINDOW"},
            )
            self.assertEqual(receipt["partition"]["superseded_rows"], 1)
            self.assertEqual(receipt["partition"]["tail_excluded_frames"], 17)
            self.assertEqual(
                receipt["partition"]["exact_nonfloor_frames"] + 17,
                receipt["partition"]["active_nonfloor_parent_frames"],
            )

    def test_materialize_writes_exact_canonical_wavs_bound_to_ledger_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, _, receipt = self.run_fixture(root, mode="materialize")
            self.assertEqual(receipt["mode"], "materialize")
            self.assertEqual(len(list((root / "windows").rglob("*.wav"))), 2)
            for row in rows:
                path = Path(row["output_wav"])
                self.assertEqual(M.sha256_file(path), row["audio_sha256"])
                with wave.open(str(path), "rb") as handle:
                    self.assertEqual(
                        (handle.getnchannels(), handle.getframerate(),
                         handle.getsampwidth(), handle.getnframes()),
                        (1, 16000, 2, 128000),
                    )

    def test_duplicate_pcm_fails_closed_without_publishing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, artifact = self.fixture(root)
            rows = M.read_jsonl(manifest)
            duplicate = dict(rows[0])
            duplicate["member"] = "Member Duplicate"
            # Give the duplicate bytes a distinct claimed full-source identity
            # so this exercises the independent PCM-identity gate.
            duplicate["source_audio_sha256"] = "f" * 64
            rows.append(duplicate)
            write_jsonl(manifest, rows)
            value = json.loads(artifact.read_text(encoding="utf-8"))
            value["source"]["manifest_sha256"] = M.sha256_file(manifest)
            artifact.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(M.ExactWindowError, "duplicate exact child PCM"):
                M.process(
                    manifest, artifact, root / "out.jsonl", root / "excluded.jsonl",
                    root / "receipt.json", mode="account",
                )
            self.assertFalse((root / "out.jsonl").exists())

    def test_stale_supersession_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, artifact = self.fixture(root)
            value = json.loads(artifact.read_text(encoding="utf-8"))
            value["source"]["canonical_supersession"]["records"][0][
                "canonical_clip_audio_sha256"
            ] = "0" * 64
            value["source"]["canonical_supersession"]["supersession_rows_sha256"] = (
                M.stable_sha256(
                    value["source"]["canonical_supersession"]["records"]
                )
            )
            artifact.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(M.ExactWindowError, "does not bind its parent"):
                M.process(
                    manifest, artifact, root / "out.jsonl", root / "excluded.jsonl",
                    root / "receipt.json", mode="account",
                )


if __name__ == "__main__":
    unittest.main()
