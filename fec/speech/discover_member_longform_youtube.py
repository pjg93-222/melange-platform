#!/usr/bin/env python3
"""Discover resumable long-form member interviews through YouTube search.

YouTube is a Google-owned web index and provides a practical fallback when a
podcast directory throttles roster-scale searches. Search metadata is only a
lead. Candidates remain identity-unverified until audio acquisition, local
ASR, an in-band naming cue, full-turn review, and speaker verification pass.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata

import yt_dlp


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROSTER = ROOT / "data/members/profile_basis_v2.json"
DEFAULT_CONGESTION = ROOT / "data/engine/congestion.json"
DEFAULT_OUT = ROOT / "data/speech/member_longform_youtube_discovery_v1.jsonl"
DEFAULT_COVERAGE = ROOT / "data/speech/member_longform_youtube_coverage_v1.json"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_youtube_discovery_v1.receipt.json"
MIN_SECONDS = 600
MAX_SECONDS = 10_800
FIRST_NAME_ALIASES = {
    "bernard": ("bernie",), "benjamin": ("ben",), "charles": ("chuck",),
    "christopher": ("chris",), "daniel": ("dan",), "donald": ("don",),
    "edward": ("ed",), "james": ("jim", "jimmy"), "john": ("jack",),
    "janice": ("jan",), "joseph": ("joe",), "margaret": ("maggie",), "matthew": ("matt",),
    "michael": ("mike",), "nicholas": ("nick",), "patrick": ("pat",),
    "richard": ("rick", "dick"), "robert": ("bob", "bobby"), "stephen": ("steve",),
    "thomas": ("tom",), "timothy": ("tim",), "william": ("bill",),
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def candidate_score(member: str, entry: dict) -> int:
    parts = normalized(member).split()
    if len(parts) < 2:
        return 0
    first, surname = parts[0], parts[-1]
    firsts = {first, *FIRST_NAME_ALIASES.get(first, ())}
    phrases = {" ".join((candidate, surname)) for candidate in firsts}
    exact = " ".join(parts)
    title = normalized(entry.get("title") or "")
    description = normalized(entry.get("description") or "")
    combined = title + " " + description
    if not (any(phrase in combined for phrase in phrases) or
            (firsts.intersection(combined.split()) and surname in combined.split())):
        return 0
    score = 0
    if any(phrase in title for phrase in phrases):
        score += 10
    elif firsts.intersection(title.split()) and surname in title.split():
        score += 7
    elif any(phrase in description for phrase in phrases) or exact in description:
        score += 4
    interview_terms = ("podcast", "interview", "conversation", "sit down", "talks with", "one on one")
    role_terms = ("senator", "representative", "congressman", "congresswoman", "rep", "sen")
    score += min(4, sum(term in title for term in interview_terms))
    combined_tokens = set(combined.split())
    title_tokens = set(title.split())
    score += min(3, sum(term in combined_tokens for term in role_terms))
    if any(term in title_tokens for term in ("ad", "commercial", "shorts", "soundbite")):
        score -= 4
    return score


def search_member(member: str, chamber: str, results_per_query: int,
                  candidates_per_member: int, delay: float,
                  deep_search: bool = False) -> tuple[list[dict], str | None, list[str]]:
    role = "Senator" if chamber == "Senate" else "Representative"
    parts = normalized(member).split()
    aliases = FIRST_NAME_ALIASES.get(parts[0], ()) if len(parts) >= 2 else ()
    queries = [f'{member} {role} podcast interview Congress']
    if deep_search:
        queries.extend((f'{member} interview', f'{member} podcast'))
        if aliases:
            queries.append(f'{aliases[0]} {parts[-1]} interview podcast')
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 2,
    }
    try:
        entries = []
        with yt_dlp.YoutubeDL(options) as client:
            for query in queries:
                if delay:
                    time.sleep(delay)
                result = client.extract_info(f"ytsearch{results_per_query}:{query}", download=False)
                entries.extend(list((result or {}).get("entries") or []))
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:300]}", queries
    scored = []
    seen = set()
    for entry in entries:
        video_id = str(entry.get("id") or "")
        duration = entry.get("duration")
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            continue
        score = candidate_score(member, entry)
        if (not video_id or video_id in seen or score <= 0 or
                not MIN_SECONDS <= duration <= MAX_SECONDS):
            continue
        seen.add(video_id)
        scored.append((score, int(entry.get("view_count") or 0), entry, duration))
    scored.sort(key=lambda value: (-value[0], -value[1], str(value[2].get("title") or "")))
    rows = []
    for rank, (score, _views, entry, duration) in enumerate(scored[:candidates_per_member], 1):
        video_id = str(entry["id"])
        rows.append({
            "schema": "member-longform-youtube-candidate-v1",
            "status": "CANDIDATE_IDENTITY_UNVERIFIED",
            "member": member,
            "queries": queries,
            "candidate_rank": rank,
            "metadata_relevance_score": score,
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": entry.get("title"),
            "channel": entry.get("channel") or entry.get("uploader"),
            "channel_id": entry.get("channel_id"),
            "duration_seconds": round(duration, 3),
            "view_count": entry.get("view_count"),
            "description_excerpt": str(entry.get("description") or "")[:500],
            "identity_authority": False,
            "profile_effect": "NONE_UNTIL_PROMOTED",
        })
    return rows, None, queries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--congestion", type=Path, default=DEFAULT_CONGESTION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--results-per-query", type=int, default=10)
    parser.add_argument("--candidates-per-member", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=0.4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-empty", action="store_true")
    parser.add_argument("--deep-search", action="store_true")
    parser.add_argument("--member", action="append", default=[])
    args = parser.parse_args()

    roster = sorted(json.loads(args.roster.read_text(encoding="utf-8-sig"))["members"])
    congestion = {
        str(row["member"]): row
        for row in json.loads(args.congestion.read_text(encoding="utf-8-sig"))
    }
    selected = roster
    if args.member:
        missing = set(args.member) - set(roster)
        if missing:
            raise SystemExit("unknown member(s): " + ", ".join(sorted(missing)))
        selected = [member for member in roster if member in set(args.member)]

    existing_rows = read_jsonl(args.out) if args.resume else []
    coverage_members = {}
    if args.resume and args.coverage.exists():
        coverage_members = json.loads(args.coverage.read_text(encoding="utf-8-sig")).get("members") or {}
    if args.resume:
        selected = [
            member for member in selected
            if member not in coverage_members or
            (args.retry_errors and (coverage_members.get(member) or {}).get("search_status") == "ERROR") or
            (args.retry_empty and int((coverage_members.get(member) or {}).get("qualifying_candidates") or 0) == 0)
        ]

    generated_at = utc_now()
    lock = threading.Lock()
    new_rows: list[dict] = []

    def persist() -> None:
        refreshed = {row["member"] for row in new_rows} | set(selected)
        # Only remove prior rows for members whose refreshed state is already present.
        completed = {member for member in refreshed if member in coverage_members}
        rows = [row for row in existing_rows if row.get("member") not in completed]
        rows.extend(row for row in new_rows if row.get("member") in completed)
        rows.sort(key=lambda row: (str(row.get("member") or ""), int(row.get("candidate_rank") or 0)))
        write_jsonl_atomic(args.out, rows)
        coverage = {
            "schema": "member-longform-youtube-coverage-v1",
            "status": "DISCOVERY_ONLY_NO_IDENTITY_AUTHORITY",
            "generated_at": generated_at,
            "members": coverage_members,
        }
        write_json_atomic(args.coverage, coverage)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                search_member, member, str((congestion.get(member) or {}).get("chamber") or "House"),
                max(1, args.results_per_query), max(1, args.candidates_per_member), args.request_delay,
                args.deep_search,
            ): member
            for member in selected
        }
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            member = futures[future]
            rows, error, queries = future.result()
            for row in rows:
                row["bioguide"] = (congestion.get(member) or {}).get("bioguide")
                row["chamber"] = (congestion.get(member) or {}).get("chamber")
                row["discovered_at"] = generated_at
            with lock:
                new_rows.extend(rows)
                coverage_members[member] = {
                    "queries": queries,
                    "search_status": "ERROR" if error else "SEARCHED",
                    "error": error,
                    "qualifying_candidates": len(rows),
                    "next_step": "download_transcribe_identity_gate" if rows else "general_web_search_required",
                }
                if number % 10 == 0 or number == len(selected):
                    persist()
                    print(f"searched {number}/{len(selected)}; candidates={len(new_rows)}", flush=True)

    if not selected:
        persist()
    final_rows = read_jsonl(args.out)
    searched = len(coverage_members)
    with_candidates = sum(int(row.get("qualifying_candidates") or 0) > 0
                          for row in coverage_members.values())
    errors = sum(row.get("search_status") == "ERROR" for row in coverage_members.values())
    receipt = {
        "schema": "member-longform-youtube-discovery-receipt-v1",
        "status": "PASS" if searched == len(roster) and not errors else "PARTIAL_VISIBLE",
        "generated_at": generated_at,
        "counts": {
            "roster_members": len(roster),
            "members_searched": searched,
            "members_with_candidates": with_candidates,
            "members_needing_general_web_search": searched - with_candidates,
            "search_errors": errors,
            "candidates": len(final_rows),
        },
        "contract": {
            "source": "Google-owned YouTube search index",
            "every_roster_member_has_search_state": searched == len(roster),
            "metadata_establishes_identity": False,
            "candidate_changes_profile": False,
            "minimum_duration_seconds": MIN_SECONDS,
            "maximum_duration_seconds": MAX_SECONDS,
            "promotion_requires_audio_hash_asr_in_band_name_full_turns_and_speaker_gate": True,
        },
        "inputs": {
            "roster_sha256": sha256(args.roster),
            "congestion_sha256": sha256(args.congestion),
        },
        "outputs": {
            "candidates": str(args.out.resolve()),
            "candidates_sha256": sha256(args.out),
            "coverage": str(args.coverage.resolve()),
            "coverage_sha256": sha256(args.coverage),
        },
    }
    write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
