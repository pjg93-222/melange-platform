"""Reconcile two supplied automated claim passes against exact timed words.

Agreement proposes candidate boundaries only. It is not independent-model
corroboration, speaker identification, human review, or semantic approval.
No API is called and no audio is cut or profile published by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from fec.speech import classify_local_youtube_turns as lexical
from fec.speech import classify_member_longform_full_turns as shared
from fec.speech import source_media_recovery as SMR


CONTRACT_VERSION = "two-automated-passes-exact-full-turn-boundaries-v1"
BINDING_KEYS = ("member", "source_id", "wav_sha256", "transcript_sha256")
DEFAULT_TOLERANCE_SECONDS = 2.0


def flattened_words(words: list[dict]) -> list[dict]:
    flat = []
    for index, word in enumerate(words):
        tokens = lexical.normalized(str(word.get("word") or "")).split()
        for offset, token in enumerate(tokens):
            flat.append({"token": token, "word_index": index,
                         "first_in_word": offset == 0, "last_in_word": offset == len(tokens) - 1})
    return flat


def anchor_boundary(text: str, claimed: float, words: list[dict], flat: list[dict],
                    *, closing: bool, tolerance: float) -> tuple[dict | None, str | None]:
    anchor = lexical.normalized(text).split()
    if not 4 <= len(anchor) <= 10:
        return None, "ANCHOR_REQUIRES_4_TO_10_TOKENS"
    matches = []
    for index in range(len(flat) - len(anchor) + 1):
        segment = flat[index:index + len(anchor)]
        if [row["token"] for row in segment] != anchor:
            continue
        endpoint = segment[-1] if closing else segment[0]
        if not endpoint["last_in_word" if closing else "first_in_word"]:
            continue
        word_index = endpoint["word_index"]
        timestamp = float(words[word_index]["end_sec" if closing else "start_sec"])
        if abs(timestamp - claimed) <= tolerance:
            matches.append({"boundary_word_index": word_index,
                            "anchor_first_word_index": segment[0]["word_index"],
                            "anchor_last_word_index": segment[-1]["word_index"]})
    if len(matches) != 1:
        return None, "ANCHOR_NOT_FOUND_NEAR_TIME" if not matches else "ANCHOR_AMBIGUOUS_NEAR_TIME"
    return matches[0], None


def resolve_claim(claim: dict, words: list[dict], flat: list[dict], tolerance: float) -> tuple[dict | None, str | None]:
    if not isinstance(claim, dict):
        return None, "INVALID_CLAIM_OBJECT"
    if (claim.get("complete_turn") is not True and claim.get("complete_turn_claim") is not True) or claim.get("uncertain") is True:
        return None, "COMPLETE_TURN_NOT_CONFIDENTLY_CLAIMED"
    if not str(claim.get("handoff_basis") or "").strip():
        return None, "BOTH_HANDOFFS_NOT_DESCRIBED"
    try:
        start, end = float(claim["start_sec"]), float(claim["end_sec"])
        if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
            raise ValueError("invalid times")
    except (KeyError, ValueError, TypeError):
        return None, "INVALID_CLAIM_TIMESTAMPS"
    opening, problem = anchor_boundary(str(claim.get("opening_words") or ""), start, words, flat,
                                       closing=False, tolerance=tolerance)
    if problem:
        return None, "OPENING_" + problem
    closing, problem = anchor_boundary(str(claim.get("closing_words") or ""), end, words, flat,
                                       closing=True, tolerance=tolerance)
    if problem:
        return None, "CLOSING_" + problem
    first, last = opening["boundary_word_index"], closing["boundary_word_index"]
    if opening["anchor_last_word_index"] > last or closing["anchor_first_word_index"] < first:
        return None, "ANCHOR_PHRASE_EXTENDS_OUTSIDE_COMPLETE_TURN"
    snapped_start, snapped_end = float(words[first]["start_sec"]), float(words[last]["end_sec"])
    if first > last or snapped_end - snapped_start < shared.MIN_COMPLETE_TURN_SECONDS:
        return None, "INVALID_OR_TOO_SHORT_CANONICAL_SPAN"
    return {"start_word_index": first, "end_word_index": last,
            "start_sec": round(snapped_start, 6), "end_sec": round(snapped_end, 6),
            "duration_seconds": round(snapped_end - snapped_start, 6),
            "opening_words": str(claim["opening_words"]), "closing_words": str(claim["closing_words"]),
            "handoff_basis": str(claim["handoff_basis"]), "complete_turn_claim": True}, None


def reconcile(source: dict, words: list[dict], pass_a: dict, pass_b: dict,
              tolerance: float = DEFAULT_TOLERANCE_SECONDS) -> tuple[list[dict], dict]:
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 10:
        raise ValueError("boundary tolerance must be finite and between zero and ten seconds")
    flat = flattened_words(words)
    maps, diagnostics = [], {}
    for label, raw in (("a", pass_a), ("b", pass_b)):
        if not isinstance(raw, dict) or any(not source.get(key) or raw.get(key) != source.get(key) for key in BINDING_KEYS):
            raise ValueError(f"pass {label} source/hash binding mismatch")
        claims = raw.get("turns", raw.get("claims"))
        if not isinstance(claims, list):
            raise ValueError(f"pass {label} lacks a turn claim list")
        if raw.get("identity_sequence") != "MATCH":
            diagnostics[label] = {"raw_claims": len(claims), "resolved": 0,
                                  "skipped": [{"reason": "NO_CONFIDENT_NAMED_HANDOFF_SEQUENCE"}]}
            maps.append({})
            continue
        resolved, skipped = {}, []
        duplicate_keys = set()
        for index, claim in enumerate(claims):
            turn, reason = resolve_claim(claim, words, flat, tolerance)
            if reason:
                skipped.append({"claim_index": index, "reason": reason})
                continue
            key = (turn["start_word_index"], turn["end_word_index"])
            if key in resolved or key in duplicate_keys:
                duplicate_keys.add(key)
                resolved.pop(key, None)
                skipped.append({"claim_index": index, "reason": "DUPLICATE_CANONICAL_BOUNDARY"})
                continue
            resolved[key] = {**turn, "claim_index": index}
        # Mutually overlapping alleged complete turns in one pass are
        # contradictory. Exclude every affected claim, not a convenient piece.
        overlapping = {key for key in resolved for other in resolved if key != other
                       and ((key[0] <= other[1] and other[0] <= key[1])
                            or (resolved[key]["start_sec"] < resolved[other]["end_sec"]
                                and resolved[other]["start_sec"] < resolved[key]["end_sec"]))}
        for key in overlapping:
            skipped.append({"claim_index": resolved[key]["claim_index"], "reason": "OVERLAPPING_COMPLETE_TURN_CLAIMS"})
            resolved.pop(key)
        diagnostics[label] = {"raw_claims": len(claims), "resolved": len(resolved), "skipped": skipped}
        maps.append(resolved)
    agreed = sorted(set(maps[0]) & set(maps[1]))
    turns = []
    for key in agreed:
        left, right = maps[0][key], maps[1][key]
        turns.append({**{field: value for field, value in left.items() if field != "claim_index"},
                      "automated_pass_claim_indexes": {"a": left["claim_index"], "b": right["claim_index"]},
                      "automated_pass_handoff_bases": {"a": left["handoff_basis"], "b": right["handoff_basis"]},
                      "agreement_basis": "identical_canonical_first_and_last_word_not_fragment_intersection"})
    diagnostics["disagreements"] = [
        {"pass": label, "claim_index": mapping[key]["claim_index"],
         "start_sec": mapping[key]["start_sec"], "end_sec": mapping[key]["end_sec"],
         "reason": "NO_EXACT_COMPLETE_BOUNDARY_AGREEMENT"}
        for label, mapping, other in (("a", maps[0], maps[1]), ("b", maps[1], maps[0]))
        for key in sorted(set(mapping) - set(other))
    ]
    if turns and not shared.cue_precedes_first_turn(source.get("cues") or [], turns):
        diagnostics["global_refusal"] = "NO_IN_BAND_CUE_WITHIN_FIRST_TURN_HANDOFF_WINDOW"
        turns = []
    return turns, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--member", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--pass-a", required=True, type=Path)
    parser.add_argument("--pass-b", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--boundary-tolerance-seconds", type=float, default=DEFAULT_TOLERANCE_SECONDS)
    args = parser.parse_args()
    if args.output.resolve() == args.receipt.resolve() or args.output.exists() or args.receipt.exists():
        parser.error("candidate output and receipt must be distinct new paths")
    if args.pass_a.resolve() == args.pass_b.resolve():
        parser.error("two distinct supplied pass files are required")
    paths = {"identity_cues": args.input, "pass_a": args.pass_a, "pass_b": args.pass_b}
    payloads = {key: path.read_bytes() for key, path in paths.items()}
    hashes = {key: hashlib.sha256(payload).hexdigest() for key, payload in payloads.items()}
    sources = [json.loads(line) for line in payloads["identity_cues"].decode("utf-8-sig").splitlines() if line.strip()]
    if (len(sources) != 1 or sources[0].get("member") != args.member
            or sources[0].get("source_id") != args.source_id
            or sources[0].get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE"
            or not sources[0].get("cues")):
        parser.error("input must be one explicitly matching, named-cue candidate source")
    source = sources[0]
    words, physical = shared.validate_source(source)
    turns, diagnostics = reconcile(source, words, json.loads(payloads["pass_a"].decode("utf-8-sig")),
                                    json.loads(payloads["pass_b"].decode("utf-8-sig")), args.boundary_tolerance_seconds)
    # Refuse a moving input before emitting anything that continuity can use.
    if any(SMR.sha256_file(path) != hashes[key] for key, path in paths.items()):
        raise ValueError("claim/selection input changed during reconciliation")
    if any(SMR.sha256_file(Path(item["path"])) != item["sha256"] for item in physical.values()):
        raise ValueError("physical input changed during reconciliation")
    generated_at = shared.utc_now()
    record = {**{key: source.get(key) for key in ("member", "source_id", "url", "wav", "wav_sha256",
              "transcript", "transcript_sha256", "source_era", "recorded_at", "published", "title", "channel",
              "current_role", "current_role_assumed_on")},
              "schema": "member-longform-full-turn-candidate-v1",
              "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED" if turns else "SCREENED_OUT",
              "turns": turns, "name_cues": source["cues"], "classified_at": generated_at,
              "classifier_runtime": "supplied-internal-automated-assistant-pass-reconciliation",
              "classifier_contract_version": CONTRACT_VERSION, "automated_assistant_passes": 2,
              "independent_models_claimed": False, "independence_verified": False,
              "human_semantic_review": False, "speaker_identity_authority": False,
              "final_profile_authority": False, "physical_inputs": physical,
              "pass_diagnostics": diagnostics, "supplied_pass_sha256": {key: hashes[key] for key in ("pass_a", "pass_b")},
              "next_required_gate": "whole-interior-speaker-continuity-and-human-boundary-review" if turns else None}
    SMR.write_jsonl_atomic(args.output, [record])
    counts = {"hard_cue_inputs": 1, "classified": 1, "members_with_full_turn_candidates": int(bool(turns)),
              "full_turn_candidates": len(turns), "total_turn_minutes": round(sum(turn["duration_seconds"] for turn in turns) / 60, 3),
              "longest_turn_seconds": max((turn["duration_seconds"] for turn in turns), default=0),
              "disagreements": len(diagnostics["disagreements"]), "errors": 0}
    receipt = {"schema": "member-longform-full-turn-candidate-receipt-v1", "status": "PASS_CANDIDATES_ONLY",
               "generated_at": generated_at, "counts": counts,
               "contract": {"version": CONTRACT_VERSION, "maximum_turn_seconds": None,
                            "multi_minute_turns_preserved_as_one_turn": True, "exact_both_boundary_agreement_required": True,
                            "anchor_matching": "non-fuzzy normalized lexical tokens at whole ASR word boundaries",
                            "boundary_tolerance_seconds": args.boundary_tolerance_seconds,
                            "two_passes_are_not_independent_model_or_speaker_proof": True,
                            "human_semantic_review": False, "speaker_identity_authority": False,
                            "profile_effect_before_speaker_gate_and_review": "NONE", "external_api_requests": 0},
               "inputs": {**{key + "_sha256": value for key, value in hashes.items()},
                          **{key + "_path": str(path.resolve()) for key, path in paths.items()}},
               "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)}}
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
