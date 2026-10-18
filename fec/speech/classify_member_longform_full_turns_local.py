#!/usr/bin/env python3
"""Propose complete member turns with a pinned local instruction model.

This is a candidate-generation fallback for environments where the external
classifier is unavailable.  It works only from hash-bound timestamped words,
preserves multi-minute answers as single turns, and confers no identity or
profile authority.  Acoustic continuity and human boundary review remain
mandatory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from fec.speech import classify_local_youtube_turns as base
from fec.speech import classify_member_longform_full_turns as shared
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = Path(r"C:\melange-data\member_longform_identity_cues_v1.jsonl")
DEFAULT_OUTPUT = Path(r"C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl")
DEFAULT_RECEIPT = Path(r"C:\melange-data\member_longform_full_turn_candidates_local_v1.receipt.json")
DEFAULT_MODEL = Path(r"C:\melange-data\models\qwen2.5-1.5b-instruct")
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
CLASSIFIER_CONTRACT_VERSION = "local-full-turn-physical-input-bounds-v5"
DEFAULT_CONTEXT_SECONDS = 1_800.0
PRE_CUE_SECONDS = 90.0
INPUT_CANDIDATE_STATUSES = {
    "HARD_IN_BAND_NAME_CUE_CANDIDATE",
    "SELECTED_HARD_NAME_CUE_CANDIDATE",
}


def context_limit(config, tokenizer) -> int:
    limits = [
        int(value) for value in (
            getattr(config, "max_position_embeddings", None),
            getattr(tokenizer, "model_max_length", None),
        ) if isinstance(value, (int, float)) and 0 < value < 10_000_000
    ]
    if not limits:
        raise ValueError("local model context limit is unavailable")
    return min(limits)


def require_token_budget(input_tokens: int, output_tokens: int, limit: int) -> None:
    if input_tokens + output_tokens > limit:
        raise ValueError(
            f"context_budget_exceeded: input={input_tokens}, reserved_output={output_tokens}, "
            f"model_limit={limit}; transcript was not truncated"
        )


def require_complete_output(generated_ids: list[int], maximum: int, eos_ids) -> None:
    endings = set(eos_ids if isinstance(eos_ids, (list, tuple, set)) else [eos_ids])
    if len(generated_ids) >= maximum and (not generated_ids or generated_ids[-1] not in endings):
        raise ValueError("output_budget_exhausted: partial response is not a complete-turn result")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def classifier_settings(args: argparse.Namespace) -> dict[str, Any]:
    """Persist the effective inference request, including its local model path."""
    return {
        "contract_version": CLASSIFIER_CONTRACT_VERSION,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_path": str(args.model.resolve()),
        "context_seconds": max(60.0, args.context_seconds),
        "segments_per_member": max(1, args.segments_per_member),
        "max_new_tokens": max(128, args.max_new_tokens),
        "cpu_threads": max(1, args.cpu_threads),
        "pre_cue_seconds": PRE_CUE_SECONDS,
    }


def classification_needs_processing(
    source: dict, existing: dict | None, settings: dict,
    *, refresh: bool = False, retry_screened_out: bool = False,
) -> bool:
    """A member name alone cannot bind a prior classification to current evidence."""
    if refresh or existing is None:
        return True
    for key in ("member", "source_id", "wav_sha256", "transcript_sha256"):
        if not source.get(key) or existing.get(key) != source.get(key):
            return True
    if existing.get("name_cues") != (source.get("cues") or []):
        return True
    if existing.get("classifier_settings") != settings:
        return True
    return (
        existing.get("status") == "LOCAL_CLASSIFIER_ERROR"
        or (retry_screened_out and existing.get("status") == "SCREENED_OUT")
    )


def source_key(row: dict) -> tuple[str, str]:
    return str(row.get("member") or ""), str(row.get("source_id") or "")


def source_index(rows: list[dict], label: str) -> dict[tuple[str, str], dict]:
    indexed = {}
    for row in rows:
        key = source_key(row)
        if not all(key) or key in indexed:
            raise ValueError(f"missing_or_duplicate_source_binding:{label}:{key}")
        indexed[key] = row
    return indexed


def current_source_cache(sources: list[dict], prior_rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Prune sources outside this input cohort, not members outside --member."""
    current = source_index(sources, "input")
    prior = source_index(prior_rows, "prior_output")
    return {key: row for key, row in prior.items() if key in current}


def physical_source_errors(sources: list[dict]) -> list[dict]:
    """Check source and retained-cache files before importing/loading a model.

    Identical physical bindings are checked once. Shared validation rejects
    out-of-recording timestamps; it never clips or rewrites transcript words.
    """
    seen = set()
    errors = []
    for source in sources:
        binding = tuple(str(source.get(key) or "") for key in (
            "member", "source_id", "wav", "wav_sha256", "transcript", "transcript_sha256"))
        if binding in seen:
            continue
        seen.add(binding)
        try:
            shared.validate_source(source)
        except Exception as exc:
            errors.append({"member": source.get("member"), "source_id": source.get("source_id"),
                           "error": str(exc)})
    return errors


def cue_segments(cues: list[dict], transcript_end: float, context_seconds: float,
                 limit: int) -> list[tuple[float, float]]:
    proposed = []
    for cue in sorted(cues, key=lambda row: float(row["start_sec"])):
        start = max(0.0, float(cue["start_sec"]) - PRE_CUE_SECONDS)
        end = min(transcript_end, float(cue["end_sec"]) + context_seconds)
        if end - start >= shared.MIN_COMPLETE_TURN_SECONDS:
            proposed.append((start, end))
    merged: list[list[float]] = []
    for start, end in proposed:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start, 3), round(end, 3)) for start, end in merged[:max(1, limit)]]


def excerpt_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [
        row for row in words
        if float(row["end_sec"]) >= start and float(row["start_sec"]) <= end
    ]


def local_prompt(member: str, cues: list[dict], start: float, end: float,
                 transcript: str) -> str:
    cue_text = "\n".join(
        f"[{float(row['start_sec']):.2f}-{float(row['end_sec']):.2f}] {row['phrase']}"
        for row in cues if start <= float(row["start_sec"]) <= end
    )
    return f"""Segment an interview transcript conservatively.

TARGET: {member}
EXCERPT: {start:.2f}-{end:.2f} seconds
IN-BAND TRANSCRIPT NAME CUES (exact, approved common form, or conservative phonetic ASR candidate):
{cue_text}

Use only the timed words below. MATCH only if a different speaker explicitly introduces or recognizes the target by name immediately before the target speaks. Return only COMPLETE TARGET TURNS: from the target's first word after another speaker hands over the floor through the target's final word before another speaker takes it back. Do not include questions, ads, narration, cross-talk, or other guests. Do not split a monologue to shorten it. There is no maximum turn length; a turn may last many minutes. Omit any turn that began before or continues after this excerpt, or whose speaker boundary is uncertain.

For each turn, opening_words must copy the exact first 4-10 transcript words. Return JSON only:
{{"identity_sequence":"MATCH|NEUTRAL|MISMATCH","identity_basis":"brief literal explanation","turns":[{{"start_sec":number,"end_sec":number,"opening_words":"exact first 4-10 words","handoff_basis":"both speaker boundaries"}}]}}

TIMED WORDS:
{transcript}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--retry-screened-out", action="store_true",
        help="Revisit only prior SCREENED_OUT rows, normally with a wider cue/context pass.",
    )
    parser.add_argument("--context-seconds", type=float, default=DEFAULT_CONTEXT_SECONDS)
    parser.add_argument("--segments-per-member", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=1_500)
    parser.add_argument("--cpu-threads", type=int, default=6)
    parser.add_argument("--preflight-only", action="store_true",
                        help="Check exact prompt token budgets without loading model weights or writing artifacts.")
    args = parser.parse_args()

    input_sha256 = SMR.sha256_file(args.input)
    all_source_rows = [
        row for row in SMR.read_jsonl(args.input, required=True)
        if row.get("status") in INPUT_CANDIDATE_STATUSES
    ]
    source_rows = [row for row in all_source_rows if not args.member or row.get("member") in set(args.member)]
    existing = current_source_cache(
        all_source_rows, SMR.read_jsonl(args.output) if args.output.exists() else [])
    settings = classifier_settings(args)
    pending = [
        row for row in source_rows
        if classification_needs_processing(
            row, existing.get(source_key(row)), settings,
            refresh=args.refresh, retry_screened_out=args.retry_screened_out,
        )
    ]

    pending_keys = {source_key(row) for row in pending}
    retained_cache = ([] if args.preflight_only else
                      [row for key, row in existing.items() if key not in pending_keys])
    # Stale rows being replaced are not admissions. Their historical paths may
    # be gone; validate the replacement source, not the superseded cache entry.
    validation_errors = physical_source_errors(all_source_rows + retained_cache)
    if validation_errors:
        print(json.dumps({
            "status": "FAIL_PREFLIGHT" if args.preflight_only else "FAIL_PHYSICAL_SOURCE_VALIDATION",
            "planned_regions": [], "errors": validation_errors,
            "model_weights_loaded": False, "transcript_clipped": False,
            "artifacts_written": False,
        }, sort_keys=True))
        return 1

    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(max(1, args.cpu_threads))
    tokenizer = AutoTokenizer.from_pretrained(str(args.model), local_files_only=True)
    config = AutoConfig.from_pretrained(str(args.model), local_files_only=True)
    token_limit = context_limit(config, tokenizer)
    if args.preflight_only:
        planned = []
        errors = []
        for source in source_rows:
            try:
                words, physical_inputs = shared.validate_source(source)
                regions = cue_segments(source.get("cues") or [], float(words[-1]["end_sec"]),
                                       max(60.0, args.context_seconds), max(1, args.segments_per_member))
                for start, end in regions:
                    message = local_prompt(str(source["member"]), source.get("cues") or [], start, end,
                                           base.format_timed_words(excerpt_words(words, start, end)))
                    rendered = tokenizer.apply_chat_template([{"role": "user", "content": message}],
                                                             tokenize=False, add_generation_prompt=True)
                    size = len(tokenizer(rendered, truncation=False)["input_ids"])
                    require_token_budget(size, max(128, args.max_new_tokens), token_limit)
                    planned.append({"member": source["member"], "start_sec": start, "end_sec": end,
                                    "source_id": source.get("source_id"), "physical_inputs": physical_inputs,
                                    "input_tokens": size, "reserved_output_tokens": max(128, args.max_new_tokens),
                                    "model_context_limit": token_limit})
            except Exception as exc:
                errors.append({"member": source["member"], "error": str(exc)})
        print(json.dumps({"status": "PASS_PREFLIGHT_ONLY" if not errors else "FAIL_PREFLIGHT",
                          "planned_regions": planned, "errors": errors}, sort_keys=True))
        return 1 if errors else 0
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model), local_files_only=True, dtype="auto", low_cpu_mem_usage=True,
    )
    model.eval()

    for number, source in enumerate(pending, 1):
        transcript = Path(str(source["transcript"])).resolve()
        record: dict[str, Any] = {
            "schema": "member-longform-full-turn-candidate-local-v1",
            "member": source["member"], "source_id": source.get("source_id"),
            "url": source.get("url"), "wav": source.get("wav"),
            "wav_sha256": source.get("wav_sha256"), "transcript": str(transcript),
            "transcript_sha256": source.get("transcript_sha256"),
            "name_cues": source.get("cues") or [],
            "classifier_model": MODEL_ID, "classifier_revision": MODEL_REVISION,
            "classifier_settings": settings,
            "classifier_runtime": "local-transformers-cpu",
            "speaker_identity_authority": False, "final_profile_authority": False,
            "classified_at": utc_now(),
        }
        try:
            words, physical_inputs = shared.validate_source(source)
            record["physical_inputs"] = physical_inputs
            segments = cue_segments(
                source.get("cues") or [], float(words[-1]["end_sec"]),
                max(60.0, args.context_seconds), max(1, args.segments_per_member),
            )
            proposed = []
            segment_results = []
            for start, end in segments:
                segment_words = excerpt_words(words, start, end)
                prompt = local_prompt(
                    str(source["member"]), source.get("cues") or [], start, end,
                    base.format_timed_words(segment_words),
                )
                messages = [{"role": "user", "content": prompt}]
                rendered = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                inputs = tokenizer(rendered, return_tensors="pt", truncation=False)
                input_tokens = int(inputs["input_ids"].shape[1])
                require_token_budget(input_tokens, max(128, args.max_new_tokens), token_limit)
                with torch.inference_mode():
                    output_ids = model.generate(
                        **inputs, max_new_tokens=max(128, args.max_new_tokens),
                        do_sample=False, repetition_penalty=1.03,
                    )
                generated_ids = output_ids[0][input_tokens:].tolist()
                require_complete_output(generated_ids, max(128, args.max_new_tokens),
                                        model.generation_config.eos_token_id)
                raw = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
                parsed = shared.parse_json(raw)
                turns = [
                    turn for claim in parsed["turns"]
                    if (turn := shared.snap_complete_turn(claim, words)) is not None
                    and start <= float(turn["start_sec"])
                    and float(turn["end_sec"]) <= end
                ]
                if (parsed["identity_sequence"] == "MATCH"
                        and shared.cue_precedes_first_turn(source.get("cues") or [], turns)):
                    proposed.extend(turns)
                segment_results.append({
                    "start_sec": start, "end_sec": end,
                    "identity_sequence": parsed["identity_sequence"],
                    "identity_basis": str(parsed.get("identity_basis") or "")[:500],
                    "candidate_turns": len(turns), "raw_response": raw[:12_000],
                    "input_tokens": input_tokens, "output_tokens": len(generated_ids),
                    "model_context_limit": token_limit, "output_complete": True,
                })
            proposed.sort(key=lambda row: (row["start_sec"], row["end_sec"]))
            turns = []
            for turn in proposed:
                if turns and turn["start_sec"] < turns[-1]["end_sec"]:
                    continue
                turns.append(turn)
            # A slow generation cannot be attached to files that changed while
            # it ran, even if the initial preflight passed.
            shared.validate_source(source)
            record.update({
                "status": (
                    "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"
                    if turns else "SCREENED_OUT"
                ),
                "turns": turns, "segments": segment_results,
                "next_required_gate": (
                    "speaker-continuity-and-complete-turn-human-review" if turns else None
                ),
            })
        except Exception as exc:
            record.update({
                "status": "LOCAL_CLASSIFIER_ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
            })
        existing[source_key(record)] = record
        SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=source_key))
        print(f"local-full-turn {number}/{len(pending)} {record['member']}: {record['status']}", flush=True)

    if not pending:
        SMR.write_jsonl_atomic(args.output, sorted(existing.values(), key=source_key))
    rows = list(existing.values())
    candidates = [row for row in rows if row.get("status") == "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"]
    durations = [float(turn["duration_seconds"]) for row in candidates for turn in row.get("turns") or []]
    counts = {
        "hard_cue_inputs": len(source_rows), "classified": len(rows),
        "current_input_sources": len(all_source_rows),
        "members_with_full_turn_candidates": len({row["member"] for row in candidates}),
        "sources_with_full_turn_candidates": len(candidates),
        "full_turn_candidates": len(durations),
        "total_turn_minutes": round(sum(durations) / 60.0, 3),
        "longest_turn_seconds": round(max(durations), 3) if durations else 0.0,
        "errors": sum(row.get("status") == "LOCAL_CLASSIFIER_ERROR" for row in rows),
    }
    if SMR.sha256_file(args.input) != input_sha256:
        raise SystemExit("input manifest changed during classification; output candidates must not be consumed")
    receipt = {
        "schema": "member-longform-full-turn-candidate-local-receipt-v1",
        "status": "PASS_CANDIDATES_ONLY" if not counts["errors"] else "PARTIAL_VISIBLE",
        "generated_at": utc_now(), "counts": counts,
        "contract": {
            "turn_definition": "target takes floor through handoff to another speaker",
            "minimum_complete_turn_seconds": shared.MIN_COMPLETE_TURN_SECONDS,
            "maximum_turn_seconds": None,
            "multi_minute_turns_preserved_as_one_turn": True,
            "oversized_transcripts_are_not_truncated": True,
            "physical_source_bounds_checked_before_model_loading": True,
            "physical_source_bindings_rechecked_after_inference": True,
            "out_of_audio_word_times_are_refused_not_clipped": True,
            "output_budget_exhaustion_is_an_error": True,
            "classified_time_regions_are_explicit_not_whole_recording_claims": True,
            "cache_key": "member_and_source_id",
            "cache_is_limited_to_current_input_sources": True,
            "member_filter_preserves_other_current_cohort_sources": True,
            "local_classifier_establishes_identity": False,
            "profile_effect_before_speaker_gate_and_human_review": "NONE",
        },
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION, "path": str(args.model.resolve())},
        "classifier_settings": settings,
        "inputs": {"identity_cues_sha256": input_sha256},
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
