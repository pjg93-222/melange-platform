#!/usr/bin/env python3
"""Discover long-form interviews published before each current leadership role.

Pre-role sources are preferred for baseline meeting-style inference.  Audio
published after the cutoff remains useful only as separately labeled,
role-conditioned behavior.  Directory metadata is discovery evidence only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from fec.speech import discover_member_longform_youtube as names
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROLES = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
DEFAULT_OUT = ROOT / "data/speech/member_leadership_prerole_discovery_v1.jsonl"
DEFAULT_COVERAGE = ROOT / "data/speech/member_leadership_prerole_coverage_v1.json"
DEFAULT_RECEIPT = ROOT / "data/speech/member_leadership_prerole_discovery_v1.receipt.json"
SEARCH_ENDPOINT = "https://itunes.apple.com/search"
MIN_SECONDS = 600.0
MAX_SECONDS = 10_800.0
ROLE_WORDS = {
    "senator", "sen", "representative", "rep", "congressman", "congresswoman",
    "congress", "governor", "mayor", "legislature", "capitol",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_date(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def name_phrases(search_name: str) -> set[str]:
    parts = names.normalized(search_name).split()
    if len(parts) < 2:
        return set()
    first, surname = parts[0], parts[-1]
    return {" ".join((candidate, surname)) for candidate in {
        first, *names.FIRST_NAME_ALIASES.get(first, ())
    }}


def candidate_score(search_name: str, result: dict) -> int:
    title = names.normalized(str(result.get("trackName") or ""))
    description = names.normalized(" ".join(str(result.get(key) or "") for key in (
        "shortDescription", "description", "collectionName"
    )))
    combined = title + " " + description
    phrases = name_phrases(search_name)
    if not any(phrase in combined for phrase in phrases):
        return 0
    score = 6 if any(phrase in title for phrase in phrases) else 3
    tokens = set(combined.split())
    score += min(3, len(tokens & ROLE_WORDS))
    score += min(3, sum(term in title for term in (
        "interview", "podcast", "conversation", "with", "joins", "one on one"
    )))
    return score


def fetch(search_name: str, limit: int) -> list[dict]:
    query = f"{search_name} Congress podcast interview"
    url = SEARCH_ENDPOINT + "?" + urllib.parse.urlencode({
        "term": query, "country": "US", "media": "podcast",
        "entity": "podcastEpisode", "limit": limit, "explicit": "Yes",
    })
    request = urllib.request.Request(url, headers={"User-Agent": "MelangeSystems/1.0 corpus-discovery"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return list(json.loads(response.read().decode("utf-8")).get("results") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--results-per-member", type=int, default=200)
    parser.add_argument("--candidates-per-member", type=int, default=10)
    parser.add_argument("--request-interval", type=float, default=3.2)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--member", action="append", default=[])
    args = parser.parse_args()

    role_doc = json.loads(args.roles.read_text(encoding="utf-8-sig"))
    roles = role_doc["members"]
    selected = sorted(roles)
    if args.member:
        selected = [member for member in selected if member in set(args.member)]
    rows = []
    coverage = {}
    next_call = 0.0
    for number, member in enumerate(selected, 1):
        role = roles[member]
        cutoff = parse_date(role["current_role_assumed_on"])
        results = []
        error = None
        for attempt in range(max(1, args.attempts)):
            delay = next_call - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_call = time.monotonic() + max(0.0, args.request_interval)
            try:
                results = fetch(str(role["search_name"]), max(1, args.results_per_member))
                error = None
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                time.sleep(min(3.0 * (attempt + 1), 12.0))
        candidates = []
        seen = set()
        for result in results:
            published_raw = str(result.get("releaseDate") or "")
            audio_url = str(result.get("episodeUrl") or "")
            try:
                published = parse_date(published_raw)
                duration = float(result.get("trackTimeMillis") or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            score = candidate_score(str(role["search_name"]), result)
            if (not audio_url or audio_url in seen or published >= cutoff or score <= 0
                    or not MIN_SECONDS <= duration <= MAX_SECONDS):
                continue
            seen.add(audio_url)
            candidates.append((score, published, result, duration))
        candidates.sort(key=lambda item: (-item[0], -item[1].timestamp()))
        for rank, (score, published, result, duration) in enumerate(
                candidates[:max(1, args.candidates_per_member)], 1):
            rows.append({
                "schema": "member-leadership-prerole-candidate-v1",
                "status": "PREROLE_CANDIDATE_IDENTITY_UNVERIFIED",
                "member": member, "current_role": role["current_role"],
                "current_role_assumed_on": role["current_role_assumed_on"],
                "cutoff_basis": role["cutoff_basis"], "search_name": role["search_name"],
                "candidate_rank": rank, "metadata_relevance_score": score,
                "published": published.isoformat(),
                "days_before_current_role": (cutoff - published).days,
                "title": result.get("trackName"), "show": result.get("collectionName"),
                "duration_seconds": round(duration, 3), "audio_url": result.get("episodeUrl"),
                "reference_url": result.get("trackViewUrl"),
                "description_excerpt": str(result.get("shortDescription") or result.get("description") or "")[:500],
                "identity_authority": False, "profile_effect": "NONE_UNTIL_PROMOTED",
            })
        coverage[member] = {
            "current_role": role["current_role"],
            "current_role_assumed_on": role["current_role_assumed_on"],
            "directory_results": len(results),
            "qualifying_prerole_candidates": min(len(candidates), max(1, args.candidates_per_member)),
            "search_status": "ERROR" if error else "SEARCHED", "error": error,
            "next_step": "acquire_and_identity_gate" if candidates else "historical_web_search_required",
        }
        print(f"leadership-prerole {number}/{len(selected)} {member}: {len(candidates)} candidates", flush=True)

    SMR.write_jsonl_atomic(args.out, rows)
    SMR.write_json_atomic(args.coverage, {
        "schema": "member-leadership-prerole-coverage-v1",
        "status": "DISCOVERY_ONLY_NO_IDENTITY_AUTHORITY", "generated_at": utc_now(),
        "members": coverage,
    })
    counts = {
        "leadership_members": len(selected), "members_searched": len(coverage),
        "members_with_prerole_candidates": sum(
            int(row["qualifying_prerole_candidates"]) > 0 for row in coverage.values()
        ),
        "members_needing_historical_web_search": sum(
            int(row["qualifying_prerole_candidates"]) == 0 for row in coverage.values()
        ),
        "search_errors": sum(row["search_status"] == "ERROR" for row in coverage.values()),
        "candidates": len(rows),
    }
    SMR.write_json_atomic(args.receipt, {
        "schema": "member-leadership-prerole-discovery-receipt-v1",
        "status": "PASS_DISCOVERY_ONLY" if not counts["search_errors"] else "PARTIAL_VISIBLE",
        "generated_at": utc_now(), "counts": counts,
        "contract": {
            "candidate_must_predate_current_role": True,
            "post_role_audio_is_role_conditioned": True,
            "metadata_establishes_identity": False,
            "candidate_changes_profile": False,
            "promotion_requires_audio_hash_asr_in_band_name_full_turns_speaker_gate_and_review": True,
        },
        "inputs": {"role_cutoffs_sha256": SMR.sha256_file(args.roles)},
        "outputs": {
            "candidates": str(args.out.resolve()), "candidates_sha256": SMR.sha256_file(args.out),
            "coverage": str(args.coverage.resolve()), "coverage_sha256": SMR.sha256_file(args.coverage),
        },
    })
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
