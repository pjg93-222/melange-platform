# -*- coding: utf-8 -*-
"""Inventory WJ/CNN/floor/HouseLive audio bindings and run guarded measurement smokes.

This is a research-only feasibility artifact.  It deliberately does not synthesize the legacy
YouTube floor audio, CNN text transcripts, and C-SPAN WJ captions into production profiles unless an
audio file is deterministically time-aligned to the same transcript unit.  ``--smoke-wj`` and
``--smoke-houselive`` download only selected member-turn intervals and test within-speaker F0
deviations; neither is a human review and therefore neither can enter
``b5_audio_descriptors_v2.json``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.parse
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moneyball_core import dump_atomic
from members.member_identity import Resolver
from paths import ROOT


OUT = os.path.join(ROOT, "data", "members", "b5_audio_source_pilot.json.candidate")
TURN_PATH = os.path.join(ROOT, "cspan_out", "turns.csv")
TRANSCRIPT_DIR = os.path.join(ROOT, "cspan_out", "transcripts")
SMOKE_DIR = os.path.join(ROOT, "tmp", "audio_source_smoke")
PITCH_MEAN = "F0semitoneFrom27.5Hz_sma3nz_amean"
HOUSELIVE_API = "https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net"
HOUSELIVE_SITE = "https://live.house.gov/"

TOPIC_RULES = {
    "economy_jobs": ("economy", "economic", "jobs", "employment", "inflation", "wages", "tax", "budget"),
    "foreign_security": ("iran", "iraq", "china", "russia", "ukraine", "military", "war", "terror", "security"),
    "health": ("health", "medicare", "medicaid", "hospital", "insurance", "covid", "drug", "abortion"),
    "elections_government": ("election", "vote", "voter", "campaign", "congress", "president", "democracy"),
    "finance_banking": ("bank", "banking", "credit union", "loan", "mortgage", "financial", "interest rate"),
    "immigration": ("immigration", "immigrant", "border", "asylum", "visa", "deport"),
    "energy_environment": ("energy", "climate", "environment", "oil", "gas", "emissions"),
}


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def topic_for(text):
    lowered = " " + str(text or "").lower() + " "
    scored = [(sum(lowered.count(token) for token in tokens), topic)
              for topic, tokens in TOPIC_RULES.items()]
    score, topic = max(scored)
    return topic if score else "other"


def load_wj_turns():
    by_video = defaultdict(list)
    with open(TURN_PATH, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if "c-span.org/program/washington-journal/" not in (row.get("url") or ""):
                continue
            try:
                row["offset"] = float(row.get("offset") or 0)
                row["seq"] = int(row.get("seq") or 0)
                row["nwords"] = int(row.get("nwords") or 0)
            except ValueError:
                continue
            by_video[row["id"]].append(row)
    return by_video


def source_inventory():
    by_video = load_wj_turns()
    wj_rows = [row for rows in by_video.values() for row in rows
               if str(row.get("is_member") or "").upper() == "TRUE" and row.get("resolved_member")]
    con = sqlite3.connect(os.path.join(ROOT, "social_media.db"))
    con.execute("PRAGMA query_only=1")
    cnn = con.execute("""select count(*),count(distinct member),count(distinct package)
                         from speeches where source='INTERVIEW' and package like '%transcripts.cnn.com%'""").fetchone()
    floor = con.execute("""select count(*),count(distinct member),count(distinct package)
                           from speeches where source='CREC'""").fetchone()
    con.close()
    local_floor = sum(1 for base, _, files in os.walk(os.path.join(ROOT, "audio_pilot"))
                      for name in files if name.lower().endswith(".wav"))
    verify = json.load(open(os.path.join(ROOT, "data", "members", "b5_audio_verify.json"), encoding="utf-8"))
    kept = sum(1 for row in verify.values() if row.get("keep") is True)
    return {
        "washington_journal": {
            "status": "BLOCKED_NO_AUDIO_BINDING", "transcript_units": len(wj_rows),
            "transcript_packages": len({row["id"] for row in wj_rows}),
            "members": len({row["resolved_member"] for row in wj_rows}),
            # Scoped away from the HouseLive smoke, which writes into
            # SMOKE_DIR/houselive/... and was being counted as Washington Journal audio.
            "local_audio_files": sum(1 for base, _, files in os.walk(SMOKE_DIR)
                                     if not base.startswith(os.path.join(SMOKE_DIR, "houselive"))
                                     for name in files if name.lower().endswith(".wav")),
            "binding": "C-SPAN timed turns and remote HLS URLs exist; no reviewed local binding by default",
        },
        "cnn": {
            "status": "BLOCKED_NO_AUDIO_BINDING", "transcript_units": int(cnn[0]),
            "transcript_packages": int(cnn[2]), "members": int(cnn[1]),
            "local_audio_files": 0,
            "binding": "CNN transcript packages have no deterministic local audio URL/file binding",
        },
        "floor": {
            "status": "BLOCKED_NO_ALIGNED_AUDIO", "transcript_units": int(floor[0]),
            "transcript_packages": int(floor[2]), "members": int(floor[1]),
            "local_audio_files": local_floor, "legacy_identity_only_kept_clips": kept,
            "binding": "legacy YouTube clips lack a transcript-unit hash/time alignment and two human reviews",
        },
        "houselive": {
            "status": "BLOCKED_NO_ALIGNED_AUDIO", "transcript_units": 0,
            "transcript_packages": 0, "members": 0, "local_audio_files": 0,
            "binding": (
                "official Clerk broadcast events pair HLS/DASH video with WebVTT and API caption "
                "offsets; an explicit dated probe is required because speaker labels can be unidentified"
            ),
            "official_site": HOUSELIVE_SITE,
            "api_routes": ["/transcripts/YYYY-MM-DD", "/broadcastevents/YYYYMMDD"],
            "fail_closed_on_unidentified_speaker": True,
        },
    }


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Moneyball research audio pilot/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Moneyball research audio pilot/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8-sig")


def parse_hls_audio_playlist(media_url):
    master = media_url.split("#", 1)[0]
    audio_url = urllib.parse.urljoin(master, "audio_0.m3u8")
    text = fetch_text(audio_url)
    init_uri = None
    segments = []
    elapsed = 0.0
    pending_duration = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-MAP:") and 'URI="' in line:
            init_uri = line.split('URI="', 1)[1].split('"', 1)[0]
        elif line.startswith("#EXTINF:"):
            pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and pending_duration is not None:
            segments.append({
                "start": elapsed, "duration": pending_duration,
                "url": urllib.parse.urljoin(audio_url, line),
            })
            elapsed += pending_duration
            pending_duration = None
    if not init_uri or not segments:
        raise ValueError("could not parse HouseLive audio media playlist")
    return {
        "url": audio_url, "sha256": sha_bytes(text.encode("utf-8")),
        "init_url": urllib.parse.urljoin(audio_url, init_uri), "segments": segments,
    }


def write_hls_window(playlist, start, duration, path):
    wanted_end = start + duration
    chosen = [(index, row) for index, row in enumerate(playlist["segments"])
              if row["start"] < wanted_end and row["start"] + row["duration"] > start]
    if not chosen:
        raise ValueError("HouseLive caption offset falls outside the audio playlist")
    first_index, first = chosen[0]
    lines = [
        "#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-VERSION:7",
        "#EXT-X-MEDIA-SEQUENCE:%d" % first_index,
        "#EXT-X-TARGETDURATION:%d" % int(math.ceil(max(row["duration"] for _, row in chosen))),
        '#EXT-X-MAP:URI="%s"' % playlist["init_url"],
    ]
    for _, row in chosen:
        lines.extend(["#EXTINF:%s," % row["duration"], row["url"]])
    lines.append("#EXT-X-ENDLIST")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return round(start - first["start"], 6), len(chosen)


def house_asset_files(events):
    files = []
    for event in events if isinstance(events, list) else [events]:
        asset = event.get("asset") or {}
        for row in asset.get("files") or []:
            if isinstance(row, dict) and str(row.get("url") or "").startswith(("https://", "http://")):
                files.append(row)
    return files


def house_roster_resolver():
    roster = [row["member"] for row in json.load(open(os.path.join(ROOT, "data", "engine", "congestion.json"), encoding="utf-8"))]
    return Resolver(roster)


def candidate_houselive_units(transcripts):
    resolver = house_roster_resolver()
    by_member = defaultdict(list)
    rows = sorted(transcripts, key=lambda row: float(row.get("offsettime") or 0))
    for index, row in enumerate(rows[:-1]):
        label = str(row.get("speaker") or "").strip()
        if not label or label.upper() == "UNIDENTIFIED SPEAKER":
            continue
        member = resolver.resolve(label)
        if not member:
            continue
        text = str(row.get("text") or "").strip()
        start = float(row.get("offsettime") or 0)
        end = float(rows[index + 1].get("offsettime") or 0)
        duration = min(35.0, end - start - .35)
        if duration < 5 or len(text.split()) < 18:
            continue
        by_member[member].append({
            "member": member, "speaker_label": label, "transcript": text,
            "topic": topic_for(text), "start_sec": round(start + .20, 3),
            "duration_sec": round(duration, 3), "caption_timestamp": row.get("timestamp"),
            "caption_offsettime": row.get("offsettime"),
        })
    eligible = []
    for member, units in by_member.items():
        topics = {row["topic"] for row in units if row["topic"] != "other"}
        if len(units) >= 5 and len(topics) >= 2:
            eligible.append((len(topics), len(units), member, units))
    if not eligible:
        raise ValueError("no unambiguous House member has five timed turns across two transcript topics")
    _, _, member, units = max(eligible, key=lambda row: (row[0], row[1], row[2]))
    selected = []
    for unit in sorted(units, key=lambda row: (row["topic"] == "other", row["topic"], -row["duration_sec"])):
        if not selected or unit["topic"] not in {item["topic"] for item in selected}:
            selected.append(unit)
        if len({item["topic"] for item in selected if item["topic"] != "other"}) >= 2:
            break
    for unit in sorted(units, key=lambda row: (-row["duration_sec"], row["start_sec"])):
        if unit not in selected:
            selected.append(unit)
        if len(selected) >= 5:
            break
    return member, selected[:5], by_member


def run_houselive_smoke(ffmpeg_path, date):
    import opensmile
    compact_date = date.replace("-", "")
    transcript_url = HOUSELIVE_API + "/transcripts/" + date
    event_url = HOUSELIVE_API + "/broadcastevents/" + compact_date
    transcripts = fetch_json(transcript_url)
    events = fetch_json(event_url)
    if not isinstance(transcripts, list) or not transcripts:
        raise ValueError("HouseLive returned no transcript units for " + date)
    files = house_asset_files(events)
    media = next((row for row in files if str(row.get("type") or "").upper() == "HLS"), None)
    caption = next((row for row in files if str(row.get("type") or "").upper() == "WEBVTT"), None)
    if not media or not caption:
        raise ValueError("HouseLive date lacks a paired HLS and WebVTT asset")
    audio_playlist = parse_hls_audio_playlist(media["url"])
    member, units, resolved = candidate_houselive_units(transcripts)
    member_dir = os.path.join(SMOKE_DIR, "houselive", date, member)
    os.makedirs(member_dir, exist_ok=True)
    smile = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                           feature_level=opensmile.FeatureLevel.Functionals)
    measured = []
    for index, unit in enumerate(units):
        wav = os.path.join(member_dir, "unit_%02d.wav" % (index + 1))
        mini_manifest = os.path.join(member_dir, "unit_%02d.m3u8" % (index + 1))
        local_seek, segment_count = write_hls_window(
            audio_playlist, unit["start_sec"], unit["duration_sec"], mini_manifest)
        command = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                   "-i", mini_manifest, "-ss", str(local_seek),
                   "-t", str(unit["duration_sec"]), "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", wav]
        run = subprocess.run(command, text=True, capture_output=True, timeout=180)
        if run.returncode or not os.path.exists(wav) or os.path.getsize(wav) < 10000:
            raise RuntimeError("HouseLive ffmpeg segment %d failed: %s" %
                               (index + 1, (run.stderr or "no output")[-500:]))
        frame = smile.process_file(wav).iloc[0]
        semitone = float(frame[PITCH_MEAN])
        hz = 27.5 * (2.0 ** (semitone / 12.0))
        if not math.isfinite(hz):
            raise ValueError("non-finite HouseLive F0")
        measured.append({**unit, "mean_f0_hz": round(hz, 6), "audio_sha256": sha_file(wav),
                         "hls_audio_segments": segment_count,
                         "transcript_sha256": sha_bytes(unit["transcript"].encode("utf-8"))})
    values = np.asarray([row["mean_f0_hz"] for row in measured], float)
    sd = float(values.std(ddof=1))
    if not math.isfinite(sd) or sd <= 0:
        raise ValueError("zero/non-finite HouseLive pitch dispersion")
    for row in measured:
        row["pitch_deviation_from_speaker_baseline_z"] = round(
            (row["mean_f0_hz"] - float(values.mean())) / sd, 6)
    named = [row for row in transcripts if str(row.get("speaker") or "").upper() != "UNIDENTIFIED SPEAKER"]
    return {
        "status": "MEASUREMENT_SMOKE_PASS", "date": date, "member": member,
        "speech_units": len(measured), "transcript_units": len(transcripts),
        "named_speaker_units": len(named), "resolved_member_n": len(resolved),
        "topics": sorted({row["topic"] for row in measured}),
        "mean_f0_hz": round(float(values.mean()), 6), "sd_f0_hz": round(sd, 6),
        "transcript_url": transcript_url, "broadcast_event_url": event_url,
        "media_asset": {key: media.get(key) for key in ("type", "id", "name", "url")},
        "caption_asset": {key: caption.get(key) for key in ("type", "id", "name", "url")},
        "audio_playlist_url": audio_playlist["url"],
        "audio_playlist_sha256": audio_playlist["sha256"],
        "topic_dictionary_sha256": sha_bytes(json.dumps(TOPIC_RULES, sort_keys=True).encode("utf-8")),
        "units": measured,
        "limitation": (
            "official timed-caption/video machinery smoke only; caption speaker identity is not "
            "two-review evidence, and current feeds may be unidentified"
        ),
    }


def candidate_wj_units():
    by_video = load_wj_turns()
    by_member = defaultdict(list)
    for video_id, rows in by_video.items():
        rows.sort(key=lambda row: (row["seq"], row["offset"]))
        meta_path = os.path.join(TRANSCRIPT_DIR, video_id + ".json")
        if not os.path.exists(meta_path):
            continue
        meta = json.load(open(meta_path, encoding="utf-8"))
        media_url = str(meta.get("media_url") or "")
        if not media_url.startswith(("https://", "http://")):
            continue
        for index, row in enumerate(rows[:-1]):
            if str(row.get("is_member") or "").upper() != "TRUE" or not row.get("resolved_member"):
                continue
            end = rows[index + 1]["offset"]
            duration = min(40.0, end - row["offset"] - .35)
            if duration < 5 or row["nwords"] < 18:
                continue
            by_member[row["resolved_member"]].append({
                "member": row["resolved_member"], "video_id": video_id,
                "source_url": row["url"], "media_url": media_url, "date": row.get("date"),
                "start_sec": round(row["offset"] + .20, 3), "duration_sec": round(duration, 3),
                "transcript": row["text"], "topic": topic_for(row["text"]), "seq": row["seq"],
            })
    eligible = []
    for member, units in by_member.items():
        videos = {row["video_id"] for row in units}
        topics = {row["topic"] for row in units if row["topic"] != "other"}
        if len(units) >= 5 and len(videos) >= 2 and len(topics) >= 2:
            eligible.append((len(videos), len(topics), len(units), member, units))
    if not eligible:
        raise ValueError("no WJ member has five timed turns across two episodes and two dictionary topics")
    _, _, _, member, units = max(eligible, key=lambda row: (row[0], row[1], row[2], row[3]))
    selected = []
    # First cover two episodes and two non-other transcript topics, then fill to five longest turns.
    for unit in sorted(units, key=lambda row: (row["topic"] == "other", row["video_id"], row["topic"], -row["duration_sec"])):
        if not selected or unit["video_id"] not in {x["video_id"] for x in selected} or unit["topic"] not in {x["topic"] for x in selected}:
            selected.append(unit)
        if len({x["video_id"] for x in selected}) >= 2 and len({x["topic"] for x in selected if x["topic"] != "other"}) >= 2:
            break
    for unit in sorted(units, key=lambda row: (-row["duration_sec"], row["video_id"], row["seq"])):
        if unit not in selected:
            selected.append(unit)
        if len(selected) >= 5:
            break
    return member, selected[:5]


def run_wj_smoke(ffmpeg_path):
    import opensmile
    member, units = candidate_wj_units()
    member_dir = os.path.join(SMOKE_DIR, member)
    os.makedirs(member_dir, exist_ok=True)
    smile = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                           feature_level=opensmile.FeatureLevel.Functionals)
    measured = []
    for index, unit in enumerate(units):
        wav = os.path.join(member_dir, "%s_%03d.wav" % (unit["video_id"], unit["seq"]))
        command = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                   "-headers", "Referer: %s\r\n" % unit["source_url"],
                   "-ss", str(unit["start_sec"]), "-i", unit["media_url"],
                   "-t", str(unit["duration_sec"]), "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", wav]
        run = subprocess.run(command, text=True, capture_output=True, timeout=120)
        if run.returncode or not os.path.exists(wav) or os.path.getsize(wav) < 10000:
            raise RuntimeError("ffmpeg segment %d failed: %s" % (index + 1, (run.stderr or "no output")[-300:]))
        frame = smile.process_file(wav).iloc[0]
        semitone = float(frame[PITCH_MEAN])
        hz = 27.5 * (2.0 ** (semitone / 12.0))
        if not math.isfinite(hz):
            raise ValueError("non-finite F0")
        measured.append({**{key: value for key, value in unit.items() if key != "media_url"},
                         "mean_f0_hz": round(hz, 6), "audio_sha256": sha_file(wav),
                         "transcript_sha256": sha_bytes(unit["transcript"].encode("utf-8"))})
    values = np.asarray([row["mean_f0_hz"] for row in measured], float)
    sd = float(values.std(ddof=1))
    if not math.isfinite(sd) or sd <= 0:
        raise ValueError("zero/non-finite pitch dispersion")
    for row in measured:
        row["pitch_deviation_from_speaker_baseline_z"] = round((row["mean_f0_hz"] - float(values.mean())) / sd, 6)
    return {
        "status": "MEASUREMENT_SMOKE_PASS", "member": member,
        "speech_units": len(measured), "episodes": len({row["video_id"] for row in measured}),
        "topics": sorted({row["topic"] for row in measured}),
        "mean_f0_hz": round(float(values.mean()), 6), "sd_f0_hz": round(sd, 6),
        "topic_dictionary_sha256": sha_bytes(json.dumps(TOPIC_RULES, sort_keys=True).encode("utf-8")),
        "units": measured,
        "limitation": "timed-caption/HLS machinery smoke only; not two-review identity/alignment evidence",
    }


def build(smoke_wj=False, smoke_houselive=False, ffmpeg_path=None, houselive_date="2025-07-15"):
    sources = source_inventory()
    smoke = None
    if smoke_wj:
        if not ffmpeg_path or not os.path.exists(ffmpeg_path):
            raise ValueError("--ffmpeg must identify an existing binary for --smoke-wj")
        smoke = run_wj_smoke(ffmpeg_path)
        sources["washington_journal"]["status"] = smoke["status"]
        sources["washington_journal"]["local_audio_files"] = smoke["speech_units"]
    house_smoke = None
    if smoke_houselive:
        if not ffmpeg_path or not os.path.exists(ffmpeg_path):
            raise ValueError("--ffmpeg must identify an existing binary for --smoke-houselive")
        house_smoke = run_houselive_smoke(ffmpeg_path, houselive_date)
        sources["houselive"].update({
            "status": house_smoke["status"],
            "transcript_units": house_smoke["transcript_units"],
            "transcript_packages": 1,
            "members": house_smoke["resolved_member_n"],
            "local_audio_files": house_smoke["speech_units"],
            "paired_caption_assets": 1,
        })
    return {
        "schema": "b5-audio-source-pilot-v1", "status": "RESEARCH_SMOKE_ONLY_NOT_PROFILE",
        "sources": sources, "wj_measurement_smoke": smoke,
        "houselive_measurement_smoke": house_smoke, "production_profile_n": 0,
        "contract": {
            "unreviewed_audio_may_enter_profiles": False,
            "within_speaker_pitch_deviation_required": True,
            "transcript_identified_topics_required": True,
            "cross_source_audio_requires_program_conditioning": True,
            "absolute_f0_or_cnn_apparent_personality_may_enter_big_five": False,
        },
    }


def self_test():
    assert topic_for("jobs and inflation hurt wages") == "economy_jobs"
    assert topic_for("the border and asylum system") == "immigration"
    assert topic_for("hello world") == "other"
    print("b5_audio_source_pilot self-test: 3 passed, 0 failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-wj", action="store_true")
    parser.add_argument("--smoke-houselive", action="store_true")
    parser.add_argument("--houselive-date", default="2025-07-15")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    doc = build(args.smoke_wj, args.smoke_houselive, args.ffmpeg, args.houselive_date)
    dump_atomic(doc, args.out, indent=1)
    print("audio source pilot: %s" % ", ".join("%s=%s" % (name, row["status"])
                                                     for name, row in doc["sources"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
