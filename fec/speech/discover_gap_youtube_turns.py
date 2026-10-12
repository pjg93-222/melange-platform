"""Discover timestamped YouTube speech turns for the verified-audio census gap.

This is deliberately a discovery/alignment stage, not an identity promotion stage.
Titles, upload dates, and channel names can rank a candidate but can never make it
verified.  A row is ``TRANSCRIPT_BOUND_CANDIDATE`` only when timestamped captions
contain a stored, member-labelled corpus turn at the locked lexical ratio.

The output is resumable: searches and caption fetches are cached independently and
the final manifest is rebuilt deterministically on every run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CENSUS = ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl"
DEFAULT_DB = ROOT / "social_media.db"
DEFAULT_ROOT = Path(r"C:\melange-data\youtube_gap_turns_v1")
DEFAULT_OUTPUT = ROOT / "data/speech/youtube_gap_turn_candidates_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/youtube_gap_turn_candidates_v1.receipt.json"
MIN_RATIO = 0.68


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def ordered_alignment(
    transcript_tokens: Sequence[str], caption_tokens: Sequence[str], *, minimum_ratio: float
) -> tuple[int, int, float] | None:
    target = [token for token in transcript_tokens if token]
    if len(target) < 12:
        return None
    anchor_len = min(5, len(target))
    anchors = [
        target[index : index + anchor_len]
        for index in range(0, min(len(target) - anchor_len + 1, 35), 5)
    ]
    candidate_starts: set[int] = set()
    for anchor in anchors:
        first = anchor[0]
        target_offset = target.index(first)
        for index, token in enumerate(caption_tokens):
            if token != first:
                continue
            probe = caption_tokens[index : index + anchor_len + 3]
            if sum(1 for value in anchor if value in probe) >= max(3, anchor_len - 1):
                candidate_starts.add(max(0, index - target_offset))
            if len(candidate_starts) >= 100:
                break
    if not candidate_starts:
        stride = max(1, len(target) // 4)
        candidate_starts.update(range(0, max(1, len(caption_tokens) - 7), stride))
    best: tuple[int, int, float] | None = None
    for begin in sorted(candidate_starts):
        for multiplier in (0.70, 0.85, 1.0, 1.15, 1.30):
            end = min(len(caption_tokens), begin + max(8, int(len(target) * multiplier)))
            if end <= begin:
                continue
            ratio = difflib.SequenceMatcher(
                None, target, list(caption_tokens[begin:end]), autojunk=False
            ).ratio()
            if best is None or ratio > best[2]:
                best = (begin, end, ratio)
    return best if best and best[2] >= minimum_ratio else None


def member_gap(census: Path, requested: set[str], target: str) -> list[dict[str, Any]]:
    field = (
        "strict_identity_prerequisite_ready"
        if target == "strict"
        else "verified_audio_profile"
    )
    rows = [row for row in read_jsonl(census) if row.get(field) is False]
    if requested:
        rows = [row for row in rows if row["member"] in requested]
    return sorted(rows, key=lambda row: row["member"])


def load_turns(db_path: Path, members: set[str]) -> dict[str, list[dict[str, Any]]]:
    output = {member: [] for member in members}
    con = sqlite3.connect("file:" + db_path.resolve().as_posix() + "?mode=ro", uri=True)
    try:
        for member in sorted(members):
            rows = con.execute(
                """SELECT rowid,source,package,nwords,text,date,chamber
                   FROM speeches WHERE member=? AND nwords>=12
                   ORDER BY CASE source WHEN 'CHRG' THEN 0 WHEN 'CREC' THEN 1
                                        WHEN 'INTERVIEW' THEN 2 ELSE 3 END,
                            nwords DESC, rowid""",
                (member,),
            ).fetchall()
            output[member] = [
                {
                    "rowid": int(row[0]),
                    "source": row[1],
                    "package": row[2],
                    "nwords": int(row[3] or 0),
                    "text": row[4] or "",
                    "date": row[5],
                    "chamber": row[6],
                }
                for row in rows
                if len(normalize_tokens(row[4] or "")) >= 12
            ]
    finally:
        con.close()
    return output


def search_queries(
    member: str, chamber: str, turns: list[dict[str, Any]], *, turn_hints: int
) -> list[str]:
    title = "senator" if chamber == "Senate" else "representative"
    queries = [
        f'"{member}" {title} remarks',
        f'"{member}" congress hearing',
        f'"{member}" C-SPAN',
        f'"{member}" floor speech',
        f'"{member}" opening statement',
        f'"{member}" official',
    ]
    # A short distinctive fragment helps surface the exact stored event.  It is
    # discovery evidence only; acceptance still requires the full alignment.
    for turn in turns[:turn_hints]:
        tokens = [token for token in normalize_tokens(turn["text"]) if len(token) > 2]
        if len(tokens) >= 8:
            queries.append(f'"{member}" "{" ".join(tokens[:8])}"')
    return queries


def run_search(query: str, limit: int) -> list[dict[str, Any]]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        str(limit),
        f"ytsearch{limit}:{query}",
    ]
    process = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    if process.returncode:
        raise RuntimeError(process.stderr[-500:] or f"yt-dlp search exited {process.returncode}")
    payload = json.loads(process.stdout)
    return list(payload.get("entries") or [])


def candidate_score(member: str, item: dict[str, Any]) -> int:
    title = " ".join(normalize_tokens(str(item.get("title") or "")))
    channel = " ".join(normalize_tokens(str(item.get("channel") or item.get("uploader") or "")))
    full = " ".join(normalize_tokens(member))
    score = 0
    if full and full in title:
        score += 12
    if full and full in channel:
        score += 8
    trusted = ("c span", "forbes breaking news", "house", "senate", "committee", "pbs news")
    if any(value in channel for value in trusted):
        score += 3
    if any(value in title for value in ("remarks", "questions", "speaks", "speech", "statement")):
        score += 2
    duration = float(item.get("duration") or 0.0)
    if 20.0 <= duration <= 3600.0:
        score += 2
    return score


def discover_searches(
    gaps: list[dict[str, Any]], turns: dict[str, list[dict[str, Any]]], root: Path,
    *, workers: int, search_limit: int, per_member: int,
    turn_hints: int,
) -> dict[str, list[dict[str, Any]]]:
    cache_dir = root / "search"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str]] = []
    for row in gaps:
        for query in search_queries(
            row["member"], row["chamber"], turns[row["member"]], turn_hints=turn_hints
        ):
            tasks.append((row["member"], query))

    def one(task: tuple[str, str]) -> tuple[str, str, list[dict[str, Any]]]:
        member, query = task
        key = hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]
        path = cache_dir / f"{key}.json"
        if path.exists():
            return member, query, json.loads(path.read_text(encoding="utf-8"))
        entries = run_search(query, search_limit)
        write_json(path, entries)
        return member, query, entries

    collected: dict[str, dict[str, dict[str, Any]]] = {row["member"]: {} for row in gaps}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(one, task): task for task in tasks}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            member, query, entries = future.result()
            for item in entries:
                video_id = str(item.get("id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    continue
                duration = float(item.get("duration") or 0.0)
                if duration and not 12.0 <= duration <= 7200.0:
                    continue
                score = candidate_score(member, item)
                if score < 12:  # Require the normalized full name in title or channel.
                    continue
                existing = collected[member].get(video_id)
                row = {
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": item.get("title"),
                    "channel": item.get("channel") or item.get("uploader"),
                    "channel_id": item.get("channel_id") or item.get("uploader_id"),
                    "duration": item.get("duration"),
                    "upload_date": item.get("upload_date"),
                    "discovery_query": query,
                    "discovery_score": score,
                }
                if existing is None or score > int(existing["discovery_score"]):
                    collected[member][video_id] = row
            if index % 20 == 0 or index == len(tasks):
                print(f"SEARCH {index}/{len(tasks)}", flush=True)
    return {
        member: sorted(values.values(), key=lambda row: (-row["discovery_score"], row["video_id"]))[
            :per_member
        ]
        for member, values in collected.items()
    }


def fetch_caption(video_id: str, root: Path) -> list[dict[str, Any]]:
    cache = root / "captions" / f"{video_id}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import requests  # type: ignore
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

    session = requests.Session()
    original_request = session.request

    def bounded_request(method, url, **kwargs):
        kwargs.setdefault("timeout", 20)
        return original_request(method, url, **kwargs)

    session.request = bounded_request  # type: ignore[method-assign]
    fetched = YouTubeTranscriptApi(http_client=session).fetch(
        video_id, languages=["en", "en-US"]
    )
    rows = [
        {"text": str(value.text), "start": float(value.start), "duration": float(value.duration)}
        for value in fetched
        if str(value.text).strip()
    ]
    write_json(cache, rows)
    return rows


def flatten_caption(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[float, float]]]:
    tokens: list[str] = []
    times: list[tuple[float, float]] = []
    for row in rows:
        values = normalize_tokens(str(row["text"]))
        start = float(row["start"])
        end = start + float(row["duration"])
        for value in values:
            tokens.append(value)
            times.append((start, end))
    return tokens, times


def best_turn_alignment(
    member_turns: list[dict[str, Any]], caption_tokens: list[str], caption_times: list[tuple[float, float]],
    *, minimum_ratio: float,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for turn in member_turns:
        alignment = ordered_alignment(
            normalize_tokens(turn["text"]), caption_tokens, minimum_ratio=minimum_ratio
        )
        if not alignment:
            continue
        begin, end, ratio = alignment
        duration = caption_times[end - 1][1] - caption_times[begin][0]
        candidate = {
            "turn": turn,
            "begin_token": begin,
            "end_token": end,
            "alignment_ratio": ratio,
            "start_sec": caption_times[begin][0],
            "end_sec": caption_times[end - 1][1],
            "duration_sec": duration,
        }
        if best is None or (ratio, duration) > (best["alignment_ratio"], best["duration_sec"]):
            best = candidate
    return best


def align_candidates(
    gaps: list[dict[str, Any]], candidates: dict[str, list[dict[str, Any]]],
    turns: dict[str, list[dict[str, Any]]], root: Path, *, workers: int, minimum_ratio: float,
) -> list[dict[str, Any]]:
    tasks = [(row, candidate) for row in gaps for candidate in candidates[row["member"]]]

    def one(task: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        gap, candidate = task
        member = gap["member"]
        try:
            caption_rows = fetch_caption(candidate["video_id"], root)
            caption_tokens, caption_times = flatten_caption(caption_rows)
            alignment = best_turn_alignment(
                turns[member], caption_tokens, caption_times, minimum_ratio=minimum_ratio
            )
            normalized_caption = " ".join(caption_tokens)
            full_name = " ".join(normalize_tokens(member))
            named = bool(full_name and full_name in normalized_caption)
            status = "TRANSCRIPT_BOUND_CANDIDATE" if alignment else "DISCOVERY_ONLY"
            output = {
                "schema": "youtube-gap-turn-candidate-v1",
                "status": status,
                "member": member,
                "bioguide": gap.get("bioguide"),
                "chamber": gap.get("chamber"),
                **candidate,
                "caption_segments": len(caption_rows),
                "caption_named_member": named,
                "identity_policy": "title/date/channel are discovery-only; stored member-labelled turn alignment required",
            }
            if alignment:
                turn = alignment.pop("turn")
                output["alignment"] = {
                    **{key: round(value, 6) if isinstance(value, float) else value for key, value in alignment.items()},
                    "transcript_rowid": turn["rowid"],
                    "transcript_source": turn["source"],
                    "transcript_package": turn["package"],
                    "transcript_text_sha256": hashlib.sha256(turn["text"].encode("utf-8")).hexdigest(),
                    "transcript_nwords": turn["nwords"],
                }
            return output
        except Exception as exc:
            return {
                "schema": "youtube-gap-turn-candidate-v1",
                "status": "CAPTION_ERROR",
                "member": member,
                "bioguide": gap.get("bioguide"),
                "chamber": gap.get("chamber"),
                **candidate,
                "error": f"{type(exc).__name__}: {exc}"[:600],
            }

    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(one, task): task for task in tasks}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(tasks):
                print(f"CAPTION {index}/{len(tasks)}", flush=True)
    return sorted(rows, key=lambda row: (row["member"], row["video_id"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--target", choices=("profile", "strict"), default="strict")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument("--per-member", type=int, default=12)
    parser.add_argument("--minimum-ratio", type=float, default=MIN_RATIO)
    parser.add_argument("--turn-hints", type=int, default=0)
    args = parser.parse_args()
    gaps = member_gap(args.census, set(args.member), args.target)
    turns = load_turns(args.db, {row["member"] for row in gaps})
    candidates = discover_searches(
        gaps, turns, args.root, workers=args.workers, search_limit=args.search_limit,
        per_member=args.per_member, turn_hints=max(0, args.turn_hints),
    )
    rows = align_candidates(
        gaps, candidates, turns, args.root, workers=args.workers,
        minimum_ratio=args.minimum_ratio,
    )
    write_jsonl(args.output, rows)
    receipt = {
        "schema": "youtube-gap-turn-candidate-receipt-v1",
        "status": "PASS_DISCOVERY_ONLY",
        "generated_at": utc_now(),
        "contract": {
            "minimum_alignment_ratio": args.minimum_ratio,
            "title_date_channel_are_identity_gates": False,
            "transcript_bound_candidates_are_final_identity_acceptances": False,
        },
        "inputs": {
            "census": str(args.census.resolve()),
            "census_sha256": sha256_file(args.census),
            "database": str(args.db.resolve()),
            "database_sha256": sha256_file(args.db),
        },
        "coverage": {
            "gap_members": len(gaps),
            "members_with_candidates": len({row["member"] for row in rows}),
            "members_with_transcript_bound_candidates": len(
                {row["member"] for row in rows if row["status"] == "TRANSCRIPT_BOUND_CANDIDATE"}
            ),
            "candidate_rows": len(rows),
            "transcript_bound_candidate_rows": sum(
                row["status"] == "TRANSCRIPT_BOUND_CANDIDATE" for row in rows
            ),
            "caption_errors": sum(row["status"] == "CAPTION_ERROR" for row in rows),
            "members_without_stored_turns": sorted(member for member, values in turns.items() if not values),
        },
        "output": str(args.output.resolve()),
        "output_sha256": sha256_file(args.output),
    }
    write_json(args.receipt, receipt)
    print(json.dumps(receipt["coverage"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
