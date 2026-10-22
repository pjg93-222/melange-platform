"""Create hash-bound local ASR transcripts for unverified YouTube holdings.

This is localization only.  A title, channel, filename, and this ASR output
have no speaker-identity authority.  The resulting word timestamps are used
to locate literal in-band introductions and safe turn interiors in later,
separately receipted stages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any
import wave


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fec.speech import source_media_recovery as SMR  # noqa: E402


DEFAULT_MANIFEST = Path(r"C:\melange-data\youtube_audio\youtube_manifest.jsonl")
DEFAULT_CENSUS = ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl"
DEFAULT_MODEL = Path(r"C:\melange-data\models\faster-whisper-small")
DEFAULT_ASR_ROOT = Path(r"C:\melange-data\youtube_audio_asr_v1")
DEFAULT_OUTPUT = ROOT / "data/speech/local_youtube_holdings_asr_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/local_youtube_holdings_asr_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def target_members(census: list[dict[str, Any]], target: str) -> set[str]:
    field = {
        "general": "verified_audio_profile",
        "strict": "strict_identity_prerequisite_ready",
        "nonfloor": "verified_nonfloor_descriptive_profile",
    }[target]
    return {str(row["member"]) for row in census if row.get(field) is not True}


def pcm16_audio(path: Path):
    """Load the locked 16 kHz mono PCM16 contract without an FFmpeg/Av decode."""
    import numpy as np

    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getframerate() != 16000
            or source.getsampwidth() != 2
        ):
            raise RuntimeError(f"source WAV violates mono/16k/PCM16 contract: {path}")
        payload = source.readframes(source.getnframes())
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


def main() -> int:
    return main_with(None)


def main_with(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--asr-root", type=Path, default=DEFAULT_ASR_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--target", choices=("general", "strict", "nonfloor"), default="general")
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--channel", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args(argv)

    # Match the host-policy shim used by source_media_recovery.run_align.
    if "av" not in sys.modules:
        import types
        av_module = types.ModuleType("av")
        av_module.audio = types.ModuleType("av.audio")
        av_module.error = types.ModuleType("av.error")

        class InvalidDataError(Exception):
            pass

        av_module.error.InvalidDataError = InvalidDataError
        av_module.open = None
        sys.modules["av"] = av_module
        sys.modules["av.audio"] = av_module.audio
        sys.modules["av.error"] = av_module.error

    from faster_whisper import WhisperModel  # type: ignore

    census = SMR.read_jsonl(args.census, required=True)
    explicit_members = set(args.member)
    # An explicit member request is an affirmative refresh, including for a
    # member who already clears the current coverage gate.  The old ordering
    # intersected explicit requests with the missing-coverage set and therefore
    # made it impossible to add a newly supplied interview to an existing
    # member's descriptive record.
    wanted = explicit_members or target_members(census, args.target)
    explicit_channels = {value.casefold() for value in args.channel}
    source_rows = SMR.read_jsonl(args.manifest, required=True)
    selected = [
        row for row in source_rows
        if str(row.get("member") or "") in wanted
        and (not explicit_members or str(row.get("member") or "") in explicit_members)
        and (not explicit_channels or str(row.get("channel") or "").casefold() in explicit_channels)
    ]
    missing_wav = sum(not Path(str(row.get("wav") or "")).is_file() for row in selected)
    selected = [row for row in selected if Path(str(row.get("wav") or "")).is_file()]
    selected.sort(key=lambda row: (
        str(row.get("channel") or "") != "C-SPAN",
        str(row.get("member") or ""),
        str(row.get("video_id") or ""),
    ))
    if args.limit:
        selected = selected[: args.limit]

    existing = {
        str(row["video_id"]): row
        for row in (SMR.read_jsonl(args.output) if args.output.exists() else [])
    }
    def reusable(row: dict[str, Any]) -> bool:
        old = existing.get(str(row["video_id"]))
        if old is None:
            return False
        try:
            wav = Path(str(row["wav"])).resolve()
            if Path(str(old["source_wav"])).resolve() != wav:
                return False
            if SMR.sha256_file(wav) != old["source_audio_sha256"]:
                return False
            transcript = Path(str(old["transcript"]))
            return transcript.is_file() and SMR.sha256_file(transcript) == old["transcript_sha256"]
        except (KeyError, OSError):
            return False

    pending = [row for row in selected if not reusable(row)]
    model = WhisperModel(
        str(args.model), device="cpu", compute_type="int8", local_files_only=True,
        cpu_threads=max(1, args.cpu_threads), num_workers=1,
    )
    args.asr_root.mkdir(parents=True, exist_ok=True)

    for number, row in enumerate(pending, 1):
        video_id = str(row["video_id"])
        wav = Path(str(row["wav"])).resolve()
        directory = args.asr_root / video_id
        acquisition = {
            "wav": str(wav),
            "wav_sha256": SMR.sha256_file(wav),
            "media_url": f"https://www.youtube.com/watch?v={video_id}",
            "reference_url": f"https://www.youtube.com/watch?v={video_id}",
            "asr_transcript": str((directory / "transcript.words.jsonl").resolve()),
            "asr_transcript_receipt": str((directory / "transcript.receipt.json").resolve()),
            "model_path": str(args.model.resolve()),
            # These are English-language congressional recordings.  Pinning
            # the known language avoids a short music/intro region causing
            # Whisper to select an unrelated language for the full file.
            "language": "en",
        }
        words, receipt = SMR.load_or_create_asr_transcript(
            acquisition, model, pcm16_audio(wav)
        )
        existing[video_id] = {
            "schema": "local-youtube-holdings-asr-v1",
            "status": "ASR_READY_LOCALIZATION_ONLY",
            "member": row["member"],
            "bioguide": row.get("bioguide"),
            "chamber": row.get("chamber"),
            "video_id": video_id,
            "url": acquisition["reference_url"],
            "title": row.get("title"),
            "channel": row.get("channel"),
            "upload_date": row.get("upload_date"),
            "measured_duration": row.get("measured_duration"),
            "source_wav": str(wav),
            "source_audio_sha256": acquisition["wav_sha256"],
            "transcript": str(Path(str(receipt["transcript"])).resolve()),
            "transcript_sha256": receipt["transcript_sha256"],
            "word_count": len(words),
            "speaker_identity_authority": False,
            "title_channel_date_identity_authority": False,
            "generated_at": utc_now(),
        }
        SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=lambda value: value["video_id"]))
        print(f"ASR {number}/{len(pending)} {row['member']} {video_id} words={len(words)}", flush=True)

    if not pending:
        SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=lambda value: value["video_id"]))
    output_rows = list(existing.values())
    selected_ids = {str(item.get("video_id")) for item in selected}
    receipt = {
        "schema": "local-youtube-holdings-asr-receipt-v1",
        "status": "INCOMPLETE_MISSING_SOURCE_WAV" if missing_wav else "PASS_LOCALIZATION_ONLY",
        "generated_at": utc_now(),
        "target_kind": args.target,
        "contract": {
            "speaker_identity_authority": False,
            "title_channel_date_identity_authority": False,
            "physical_hash_bound": True,
            "word_timestamps_present": True,
        },
        "inputs": {
            "manifest": {"path": str(args.manifest.resolve()), "sha256": SMR.sha256_file(args.manifest)},
            "census": {"path": str(args.census.resolve()), "sha256": SMR.sha256_file(args.census)},
            "model": str(args.model.resolve()),
        },
        "coverage": {
            "selected": len(selected),
            "missing_wav": missing_wav,
            "ready": sum(str(row.get("video_id")) in selected_ids for row in output_rows),
            "members": len({row["member"] for row in output_rows if str(row.get("video_id")) in selected_ids}),
        },
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["coverage"], sort_keys=True))
    return 1 if missing_wav else 0


if __name__ == "__main__":
    raise SystemExit(main())
