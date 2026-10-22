#!/usr/bin/env python3
"""Discover public long-form podcast candidates for the full member roster.

This is discovery only.  Apple Search metadata can put an episode in the
review queue, but it cannot establish who speaks.  Promotion still requires a
physical audio hash, transcript-localized in-band naming, reviewed target
turns, and the separate speaker-continuity gate.
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROSTER = ROOT / "data/members/profile_basis_v2.json"
DEFAULT_OUT = ROOT / "data/speech/member_longform_discovery_v1.jsonl"
DEFAULT_COVERAGE = ROOT / "data/speech/member_longform_discovery_coverage_v1.json"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_discovery_v1.receipt.json"
DEFAULT_FALLBACK = ROOT / "data/speech/member_longform_google_fallback_queue_v1.jsonl"
SEARCH_ENDPOINT = "https://itunes.apple.com/search"
MIN_SECONDS = 600
MAX_SECONDS = 10_800


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
            # ASCII escaping keeps Unicode line/paragraph separators from becoming
            # apparent JSONL record breaks in consumers that use splitlines().
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    os.replace(temporary, path)


class RateLimiter:
    """Serialize directory calls so the run honors Apple's public API guidance."""

    def __init__(self, interval_seconds: float):
        self.interval_seconds = max(0.0, interval_seconds)
        self.lock = threading.Lock()
        self.next_call = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_call:
                time.sleep(self.next_call - now)
            self.next_call = time.monotonic() + self.interval_seconds


def fetch(member: str, limit: int, limiter: RateLimiter, attempts: int) -> tuple[str, list[dict], str | None]:
    query = f"{member} interview"
    url = SEARCH_ENDPOINT + "?" + urlencode({
        "term": query,
        "country": "US",
        "media": "podcast",
        "entity": "podcastEpisode",
        "limit": limit,
        "explicit": "Yes",
    })
    error = None
    for attempt in range(attempts):
        try:
            limiter.wait()
            request = Request(url, headers={"User-Agent": "MelangeSystems/1.0 corpus-discovery"})
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return query, list(payload.get("results") or []), None
        except Exception as exc:  # discovery errors remain visible per member
            error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if "429" in error:
                time.sleep(6.0 * (attempt + 1))
            else:
                time.sleep(1.0 * (attempt + 1))
    return query, [], error


def candidate_score(member: str, result: dict) -> int:
    parts = normalized(member).split()
    if len(parts) < 2:
        return 0
    first, surname = parts[0], parts[-1]
    title = normalized(str(result.get("trackName") or ""))
    description = normalized(" ".join(str(result.get(key) or "")
                                      for key in ("shortDescription", "description")))
    combined = title + " " + description
    if first not in combined or surname not in combined:
        return 0
    exact = normalized(member)
    score = 0
    if exact in title:
        score += 6
    elif first in title and surname in title:
        score += 5
    elif surname in title:
        score += 3
    elif exact in description:
        score += 2
    guest_terms = {"interview", "with", "joins", "conversation", "senator", "representative", "congress"}
    score += min(2, sum(term in title for term in guest_terms))
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--fallback", type=Path, default=DEFAULT_FALLBACK)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--request-interval", type=float, default=3.2,
                        help="Seconds between Apple Search API calls; Apple documents approximately 20/minute.")
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--resume-errors", action="store_true",
                        help="Keep prior successful rows and retry only members whose last search errored.")
    parser.add_argument("--emit-from-existing", action="store_true",
                        help="Rewrite receipts and the Google fallback queue from existing coverage without network calls.")
    parser.add_argument("--results-per-member", type=int, default=25)
    parser.add_argument("--candidates-per-member", type=int, default=3)
    parser.add_argument("--member", action="append", default=[])
    args = parser.parse_args()

    roster_doc = json.loads(args.roster.read_text(encoding="utf-8-sig"))
    roster_members = sorted((roster_doc.get("members") or {}).keys())
    members = list(roster_members)
    if args.member:
        wanted = set(args.member)
        missing = wanted - set(members)
        if missing:
            raise SystemExit("unknown member(s): " + ", ".join(sorted(missing)))
        members = [name for name in members if name in wanted]
    prior_coverage = {}
    prior_rows = []
    resume_mode = args.resume_errors or args.emit_from_existing
    if resume_mode or args.member:
        have_coverage, have_candidates = args.coverage.exists(), args.out.exists()
        if have_coverage != have_candidates:
            raise SystemExit("incremental mode requires both prior coverage and candidate outputs; incomplete pair preserved")
        have_prior = have_coverage and have_candidates
        if resume_mode and not have_prior:
            raise SystemExit("resume/emit mode requires the prior coverage and candidate outputs")
        # A single-member refresh merges into prior outputs instead of truncating them.
        if have_prior:
            prior_coverage = json.loads(args.coverage.read_text(encoding="utf-8-sig")).get("members") or {}
            prior_rows = [json.loads(line) for line in args.out.read_text(encoding="utf-8-sig").split("\n") if line.strip()]
        if resume_mode:
            members = (
                [] if args.emit_from_existing else
                [name for name in members if (prior_coverage.get(name) or {}).get("search_status") == "ERROR"]
            )
            if args.member:
                wanted = set(args.member)
                members = [name for name in members if name in wanted]
            if args.resume_errors and not members:
                print("no prior search errors remain", flush=True)

    generated_at = utc_now()
    discovered: dict[str, tuple[str, list[dict], str | None]] = {}
    limiter = RateLimiter(args.request_interval)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(fetch, member, args.results_per_member, limiter, max(1, args.attempts)): member
            for member in members
        }
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            member = futures[future]
            discovered[member] = future.result()
            if number % 50 == 0 or number == len(members):
                print(f"searched {number}/{len(members)}", flush=True)

    # A failed retry must not erase a member's prior good search; record the failure beside it.
    failed_retries = {
        name for name in members
        if discovered[name][2] and (prior_coverage.get(name) or {}).get("search_status") == "SEARCHED"
    }
    refreshed_members = set(members) - failed_retries
    rows = [row for row in prior_rows if row.get("member") not in refreshed_members]
    coverage_members = dict(prior_coverage)
    for member in members:
        query, results, error = discovered[member]
        if member in failed_retries:
            coverage_members[member] = {**prior_coverage[member], "last_refresh_error": error}
            continue
        candidates = []
        seen_urls = set()
        for result in results:
            duration = int(result.get("trackTimeMillis") or 0) / 1000.0
            audio_url = str(result.get("episodeUrl") or "")
            score = candidate_score(member, result)
            if not audio_url or audio_url in seen_urls or score <= 0:
                continue
            if not MIN_SECONDS <= duration <= MAX_SECONDS:
                continue
            seen_urls.add(audio_url)
            candidates.append((score, str(result.get("releaseDate") or ""), result, duration))
        candidates.sort(key=lambda item: (-item[0], item[1], str(item[2].get("trackName") or "")))
        selected = candidates[: max(1, args.candidates_per_member)]
        for rank, (score, _date, result, duration) in enumerate(selected, 1):
            rows.append({
                "schema": "member-longform-discovery-candidate-v1",
                "status": "CANDIDATE_IDENTITY_UNVERIFIED",
                "member": member,
                "query": query,
                "candidate_rank": rank,
                "metadata_relevance_score": score,
                "title": result.get("trackName"),
                "show": result.get("collectionName"),
                "published": result.get("releaseDate"),
                "duration_seconds": round(duration, 3),
                "audio_url": result.get("episodeUrl"),
                "reference_url": result.get("trackViewUrl"),
                "description_excerpt": str(result.get("shortDescription") or result.get("description") or "")[:500],
                "discovered_at": generated_at,
                "identity_authority": False,
            })
        coverage_members[member] = {
            "query": query,
            "search_status": "ERROR" if error else "SEARCHED",
            "error": error,
            "directory_results": len(results),
            "qualifying_candidates": len(selected),
            "next_step": (
                "download_transcribe_identity_gate"
                if selected else "google_youtube_and_official-site_fallback_required"
            ),
        }

    rows.sort(key=lambda row: (str(row.get("member") or ""), int(row.get("candidate_rank") or 0)))
    write_jsonl_atomic(args.out, rows)
    fallback_rows = []
    for member in roster_members:
        state = coverage_members.get(member) or {
            "query": f"{member} interview",
            "search_status": "NOT_SEARCHED",
            "error": "missing coverage row",
            "directory_results": 0,
            "qualifying_candidates": 0,
            "next_step": "google_youtube_and_official-site_fallback_required",
        }
        if state.get("qualifying_candidates", 0) > 0 and state.get("search_status") != "ERROR":
            continue
        fallback_rows.append({
            "schema": "member-longform-google-fallback-v1",
            "status": "SEARCH_REQUIRED",
            "member": member,
            "directory_search_status": state.get("search_status"),
            "directory_error": state.get("error"),
            "google_queries": [
                f'"{member}" (podcast OR "long-form interview") Congress',
                f'site:youtube.com "{member}" (podcast OR interview)',
                f'site:house.gov OR site:senate.gov "{member}" podcast interview',
            ],
            "promotion_rule": "A search result is only a lead; download, hash, ASR, in-band name, reviewed target turns, and speaker continuity remain required.",
        })
    write_jsonl_atomic(args.fallback, fallback_rows)
    coverage = {
        "schema": "member-longform-discovery-coverage-v1",
        "status": "DISCOVERY_ONLY_NO_IDENTITY_AUTHORITY",
        "generated_at": generated_at,
        "members_searched": len(coverage_members),
        "members_with_candidates": sum(row["qualifying_candidates"] > 0 for row in coverage_members.values()),
        "members_needing_fallback": sum(row["qualifying_candidates"] == 0 for row in coverage_members.values()),
        "search_errors": sum(row["search_status"] == "ERROR" for row in coverage_members.values()),
        "members": coverage_members,
    }
    write_json_atomic(args.coverage, coverage)
    every_member_searched = set(coverage_members) == set(roster_members)
    receipt = {
        "schema": "member-longform-discovery-receipt-v1",
        "status": "PASS" if not coverage["search_errors"] and every_member_searched else "PARTIAL_SEARCH_ERRORS_VISIBLE",
        "generated_at": generated_at,
        "contract": {
            "every_roster_member_has_a_search_state": every_member_searched,
            "apple_api_rate_limit_respected": args.request_interval >= 3.0,
            "metadata_or_title_establishes_identity": False,
            "candidate_may_modify_profile": False,
            "promotion_requires_audio_hash_transcript_name_and_speaker_gate": True,
        },
        "counts": {
            "members_searched": len(coverage_members),
            "members_with_candidates": coverage["members_with_candidates"],
            "members_needing_fallback": coverage["members_needing_fallback"],
            "candidates": len(rows),
            "search_errors": coverage["search_errors"],
        },
        "inputs": {"roster": str(args.roster.resolve()), "roster_sha256": sha256(args.roster)},
        "outputs": {
            "candidates": str(args.out.resolve()),
            "candidates_sha256": sha256(args.out),
            "coverage": str(args.coverage.resolve()),
            "coverage_sha256": sha256(args.coverage),
            "google_fallback_queue": str(args.fallback.resolve()),
            "google_fallback_queue_sha256": sha256(args.fallback),
        },
    }
    write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
