#!/usr/bin/env python3
"""Propose complete member turns from hard-cue long-form transcripts.

The semantic unit is the whole member turn, from the first word after a
speaker handoff through the last word before another speaker takes the floor.
There is no duration ceiling: a valid turn may last seconds or many minutes.
Results remain identity-unverified until the separate speaker-continuity gate.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
import wave

from fec.speech import classify_local_youtube_turns as base
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/speech/member_longform_identity_cues_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data/speech/member_longform_full_turn_candidates_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_full_turn_candidates_v1.receipt.json"
DEFAULT_KEY = ROOT / "fec/anthropic_key.txt"
MODEL = "claude-haiku-4-5"
CLASSIFIER_CONTRACT_VERSION = "original-complete-turn-source-bound-v2"
MIN_COMPLETE_TURN_SECONDS = 3.0
INPUT_CANDIDATE_STATUSES = {
    "HARD_IN_BAND_NAME_CUE_CANDIDATE",
    "SELECTED_HARD_NAME_CUE_CANDIDATE",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prompt(member: str, cues: list[dict], transcript: str) -> str:
    cue_text = "\n".join(
        f"[{float(row['start_sec']):.2f}-{float(row['end_sec']):.2f}] {row['phrase']}"
        for row in cues
    )
    return f"""You are conservatively segmenting one interview using only its timestamped transcript.

TARGET PERSON: {member}

The deterministic pre-screen found these in-band transcript name cues. They
may be exact, use an approved common first-name form, or be a conservative
phonetic ASR rendering that still requires listening:
{cue_text}

Do not use a title, channel, filename, date, topic, party, or policy position as identity evidence. Determine whether a host/narrator explicitly introduces or recognizes the target immediately before the target begins speaking. If that speaker sequence is not clear, return NEUTRAL and no turns.

If it is clear, return every confidently bounded COMPLETE TARGET TURN. A complete turn starts at the target's first word after another speaker hands over the floor and ends at the target's last word before another speaker takes it back. Preserve one uninterrupted monologue as one turn. Never split a turn to make it shorter. There is NO MAXIMUM TURN LENGTH: turns may last seconds, several minutes, or many minutes. Exclude interviewer questions, introductions, advertisements, narration, cross-talk, and other guests. Omit a turn if either speaker boundary is uncertain.

For each turn, copy its exact first 4-10 transcript words into opening_words. Return JSON only:
{{"identity_sequence":"MATCH|NEUTRAL|MISMATCH","identity_basis":"brief literal speaker-sequence explanation","turns":[{{"start_sec":number,"end_sec":number,"opening_words":"exact first 4-10 target words","handoff_basis":"brief description of both speaker boundaries"}}]}}

TIMED TRANSCRIPT:
{transcript}"""


def parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        embedded = re.search(r"\{.*\}", text, re.S)
        if embedded:
            text = embedded.group(0)
    value = json.loads(text)
    if value.get("identity_sequence") not in {"MATCH", "NEUTRAL", "MISMATCH"}:
        raise ValueError("invalid identity_sequence")
    if not isinstance(value.get("turns"), list):
        raise ValueError("invalid turns")
    return value


def snap_complete_turn(claim: dict, words: list[dict]) -> dict | None:
    try:
        start = float(claim["start_sec"])
        end = float(claim["end_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if not words or not (0.0 <= start < end <= float(words[-1]["end_sec"]) + 1.0):
        return None
    starts = [float(row["start_sec"]) for row in words]
    ends = [float(row["end_sec"]) for row in words]
    snapped_start = min(starts, key=lambda value: abs(value - start))
    snapped_end = min(ends, key=lambda value: abs(value - end))
    opening = base.normalized(str(claim.get("opening_words") or "")).split()
    if not 4 <= len(opening) <= 10:
        return None
    nearby_tokens: list[str] = []
    nearby_starts: list[float] = []
    for row in words:
        if float(row["end_sec"]) < snapped_start - 10.0:
            continue
        if float(row["start_sec"]) > snapped_start + 10.0:
            break
        for token in base.normalized(str(row.get("word") or "")).split():
            nearby_tokens.append(token)
            nearby_starts.append(float(row["start_sec"]))
    exact_start = None
    for index in range(len(nearby_tokens) - len(opening) + 1):
        if nearby_tokens[index:index + len(opening)] == opening:
            exact_start = nearby_starts[index]
            break
    if exact_start is None:
        return None
    snapped_start = exact_start
    duration = snapped_end - snapped_start
    if duration < MIN_COMPLETE_TURN_SECONDS:
        return None
    return {
        "start_sec": round(snapped_start, 6),
        "end_sec": round(snapped_end, 6),
        "duration_seconds": round(duration, 6),
        "opening_words": str(claim.get("opening_words") or "")[:200],
        "handoff_basis": str(claim.get("handoff_basis") or "")[:500],
        "complete_turn_claim": True,
    }


def cue_precedes_first_turn(cues: list[dict], turns: list[dict]) -> bool:
    if not cues or not turns:
        return False
    first = float(turns[0]["start_sec"])
    return any(0.0 <= first - float(cue["end_sec"]) <= 300.0 for cue in cues)


def row_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def source_key(row: dict) -> tuple[str, str]:
    key = (str(row.get("member") or ""), str(row.get("source_id") or ""))
    if not all(key):
        raise ValueError("source requires member and source_id")
    return key


def settings(max_tokens: int, timeout: float) -> dict:
    return {
        "contract_version": CLASSIFIER_CONTRACT_VERSION, "model": MODEL,
        "max_tokens": max_tokens, "temperature": 0, "timeout_seconds": timeout,
        "max_retries": 0, "prompt_truncation": False,
        "prompt_template_sha256": hashlib.sha256(prompt("", [], "").encode("utf-8")).hexdigest(),
    }


def needs_processing(source: dict, existing: dict | None, classifier_settings: dict,
                     *, refresh=False, retry_errors=False) -> bool:
    if refresh or existing is None:
        return True
    if source_key(source) != source_key(existing):
        return True
    if any(not source.get(key) or source[key] != existing.get(key) for key in ("wav_sha256", "transcript_sha256")):
        return True
    if existing.get("source_input_sha256") != row_hash(source) or existing.get("classifier_settings") != classifier_settings:
        return True
    return retry_errors and existing.get("status") in {"CLASSIFIER_ERROR", "CLASSIFIER_RESPONSE_REFUSED"}


def validate_source(source: dict) -> tuple[list[dict], dict]:
    """Validate full physical inputs before spending a request; never truncate."""
    source_key(source)
    paths = {"wav": Path(str(source.get("wav") or "")).resolve(),
             "transcript": Path(str(source.get("transcript") or "")).resolve()}
    for kind, path in paths.items():
        if not path.is_file() or not source.get(kind + "_sha256") or SMR.sha256_file(path) != source[kind + "_sha256"]:
            raise ValueError(f"{kind}_hash_mismatch")
    with wave.open(str(paths["wav"]), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("source_wav_contract")
        duration = audio.getnframes() / audio.getframerate()
    words = SMR.read_jsonl(paths["transcript"], required=True)
    if not words:
        raise ValueError("empty_transcript")
    previous = -1.0
    for word in words:
        start, end = float(word["start_sec"]), float(word["end_sec"])
        if not all(math.isfinite(value) for value in (start, end)) or not (0 <= start <= end <= duration + .1) or start < previous:
            raise ValueError("transcript_timestamp_bounds_or_order")
        previous = start
    return words, {kind: {"path": str(path), "sha256": source[kind + "_sha256"]} for kind, path in paths.items()}


def classify_source(source: dict, api_key: str, classifier_settings: dict, input_sha256: str,
                    *, client_factory=None) -> dict:
    record = {
        "schema": "member-longform-full-turn-candidate-v1",
        **{key: source.get(key) for key in ("member", "source_id", "url", "wav", "wav_sha256", "transcript",
                                          "transcript_sha256", "title", "channel", "recorded_at", "published",
                                          "source_era", "current_role", "current_role_assumed_on")},
        "name_cues": source.get("cues") or [], "source_input_sha256": row_hash(source),
        "input_manifest_sha256": input_sha256, "classifier_settings": classifier_settings,
        "classifier_model": MODEL, "speaker_identity_authority": False,
        "final_profile_authority": False, "human_semantic_review": False,
        "classified_at": utc_now(), "api_requests_attempted": 0,
        "billing_cost_usd": None, "billing_cost_basis": "Provider response does not report an actual billed amount.",
    }
    try:
        words, physical_inputs = validate_source(source)
        timed_transcript = base.format_timed_words(words)
        request_prompt = prompt(str(source["member"]), source.get("cues") or [], timed_transcript)
        record.update(physical_inputs=physical_inputs, transcript_word_count=len(words),
                      transcript_start_sec=float(words[0]["start_sec"]), transcript_end_sec=float(words[-1]["end_sec"]),
                      prompt_characters=len(request_prompt),
                      prompt_sha256=hashlib.sha256(request_prompt.encode("utf-8")).hexdigest())
        if client_factory is None:
            from anthropic import Anthropic
            client_factory = Anthropic
        client = client_factory(api_key=api_key, timeout=classifier_settings["timeout_seconds"], max_retries=0)
        record["api_requests_attempted"] = 1
        response = client.messages.create(model=MODEL, max_tokens=classifier_settings["max_tokens"], temperature=0,
                                          messages=[{"role": "user", "content": request_prompt}])
        usage = getattr(response, "usage", None)
        record["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
        record["response_id"] = getattr(response, "id", None)
        record["provider_model"] = getattr(response, "model", None)
        record["request_id"] = getattr(response, "_request_id", None)
        record["stop_reason"] = getattr(response, "stop_reason", None)
        raw = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
        record["raw_response"] = raw
        if record["stop_reason"] != "end_turn":
            return {**record, "status": "CLASSIFIER_RESPONSE_REFUSED", "turns": [],
                    "refusal_reason": "truncated_max_tokens" if record["stop_reason"] == "max_tokens" else "unexpected_stop_reason"}
        # A completed response still cannot be attached to a changed input.
        validate_source(source)
        parsed = parse_json(raw)
        proposed = [snapped for claim in parsed["turns"] if (snapped := snap_complete_turn(claim, words)) is not None]
        proposed.sort(key=lambda turn: turn["start_sec"])
        nonoverlap = []
        for turn in proposed:
            if nonoverlap and turn["start_sec"] < nonoverlap[-1]["end_sec"]:
                continue
            nonoverlap.append(turn)
        accepted = parsed["identity_sequence"] == "MATCH" and cue_precedes_first_turn(source.get("cues") or [], nonoverlap) and bool(nonoverlap)
        return {
            **record, "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED" if accepted else "SCREENED_OUT",
            "identity_sequence": parsed["identity_sequence"], "identity_basis": str(parsed.get("identity_basis") or "")[:1000],
            "turns": nonoverlap if accepted else [], "proposed_turn_claims": len(parsed["turns"]),
            "boundary_or_overlap_claims_omitted": len(parsed["turns"]) - len(nonoverlap),
            "next_required_gate": "speaker-continuity-and-complete-turn-human-review" if accepted else None,
        }
    except Exception as exc:
        message = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
        return {**record, "status": "CLASSIFIER_ERROR", "turns": [],
                "error": f"{type(exc).__name__}: {message[:1000]}",
                "provider_http_status": getattr(exc, "status_code", None)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--retry-errors", action="store_true", help="Explicitly authorize another request for unchanged failed/refused rows.")
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-requests", type=int, help="Refuse the run before API use if more new recording requests are needed.")
    args = parser.parse_args()

    if args.max_output_tokens < 1 or args.timeout_seconds <= 0 or (args.max_requests is not None and args.max_requests < 0):
        parser.error("token and timeout limits must be positive; max-requests must be nonnegative")
    input_sha256 = SMR.sha256_file(args.input)
    rows = SMR.read_jsonl(args.input, required=True)
    wanted = set(args.member)
    all_inputs = [row for row in rows if row.get("status") in INPUT_CANDIDATE_STATUSES]
    selected = [row for row in all_inputs if not wanted or row.get("member") in wanted]
    all_keys = [source_key(row) for row in all_inputs]
    if len(set(all_keys)) != len(all_keys):
        raise SystemExit("duplicate input member/source_id")
    all_keys = set(all_keys)
    existing = {}
    for row in SMR.read_jsonl(args.output) if args.output.exists() else []:
        key = source_key(row)
        if key in existing:
            raise SystemExit("duplicate output member/source_id")
        # Keep every current input's prior row; --member only narrows `pending`, never the cache.
        if key in all_keys:
            existing[key] = row
    classifier_settings = settings(args.max_output_tokens, args.timeout_seconds)
    pending = [row for row in selected if needs_processing(row, existing.get(source_key(row)), classifier_settings,
                                                         refresh=args.refresh, retry_errors=args.retry_errors)]
    if args.max_requests is not None and len(pending) > args.max_requests:
        raise SystemExit(f"request budget exceeded before API use: {len(pending)} > {args.max_requests}")
    api_key = args.key.read_text(encoding="utf-8").strip() if pending else ""
    if pending and not api_key:
        raise SystemExit("API key file is empty")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(classify_source, row, api_key, classifier_settings, input_sha256): row for row in pending}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "schema": "member-longform-full-turn-candidate-v1",
                    "member": source["member"],
                    "source_id": source["source_id"],
                    "status": "CLASSIFIER_ERROR",
                    "error": f"{type(exc).__name__}: {str(exc).replace(api_key, '[REDACTED]')[:1000]}",
                    "speaker_identity_authority": False,
                    "final_profile_authority": False,
                    "classified_at": utc_now(),
                }
            existing[source_key(result)] = result
            SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=source_key))
            print(f"full-turn {number}/{len(pending)} {result['member']}: {result['status']}", flush=True)

    if not pending:
        SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=source_key))
    if SMR.sha256_file(args.input) != input_sha256:
        raise SystemExit("input manifest changed during classification; output candidates must not be consumed")
    output = list(existing.values())
    candidate_rows = [row for row in output if row.get("status") == "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"]
    durations = [float(turn["duration_seconds"]) for row in candidate_rows for turn in row.get("turns") or []]
    counts = {
        "hard_cue_inputs": len(selected),
        "classified": len(output),
        "members_with_full_turn_candidates": len({row["member"] for row in candidate_rows}),
        "sources_with_full_turn_candidates": len(candidate_rows),
        "new_recording_requests": sum(int(existing[source_key(row)].get("api_requests_attempted") or 0) for row in pending),
        "full_turn_candidates": len(durations),
        "total_turn_minutes": round(sum(durations) / 60.0, 3),
        "longest_turn_seconds": round(max(durations), 3) if durations else 0.0,
        "errors": sum(row.get("status") == "CLASSIFIER_ERROR" for row in output),
        "response_refusals": sum(row.get("status") == "CLASSIFIER_RESPONSE_REFUSED" for row in output),
    }
    receipt = {
        "schema": "member-longform-full-turn-candidate-receipt-v1",
        "status": "PASS_CANDIDATES_ONLY" if not (counts["errors"] or counts["response_refusals"]) else "PARTIAL_VISIBLE",
        "generated_at": utc_now(),
        "counts": counts,
        "contract": {
            "turn_definition": "target takes floor through handoff to another speaker",
            "minimum_complete_turn_seconds": MIN_COMPLETE_TURN_SECONDS,
            "maximum_turn_seconds": None,
            "multi_minute_turns_preserved_as_one_turn": True,
            "classifier_establishes_identity": False,
            "source_and_settings_bound_resume": True,
            "transcript_prompt_truncated": False,
            "api_automatic_retries": 0,
            "max_tokens_responses_are_refused": True,
            "profile_effect_before_speaker_gate_and_review": "NONE",
        },
        "classifier_settings": classifier_settings,
        "inputs": {"identity_cues_path": str(args.input.resolve()), "identity_cues_sha256": input_sha256},
        "usage": [{"member": row["member"], "source_id": row["source_id"], "usage": row.get("usage"),
                   "response_id": row.get("response_id"), "billing_cost_usd": row.get("billing_cost_usd")}
                  for row in output if row.get("api_requests_attempted")],
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
