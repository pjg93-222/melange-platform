"""Screen local YouTube ASR for hard identity signals and target-only spans.

This is a conservative perception/routing stage.  It never accepts identity or
cuts corpus audio.  A candidate requires a literal, timestamped in-band naming
signal and at least one purported target span long enough to survive a two-
second inset on both sides.  Exact PCM cutting and acoustic rejection gates are
separate downstream stages.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/speech/local_youtube_holdings_asr_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data/speech/local_youtube_turn_candidates_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/local_youtube_turn_candidates_v1.receipt.json"
DEFAULT_KEY = ROOT / "fec/anthropic_key.txt"
MODEL = "claude-haiku-4-5"
MIN_SPAN_SECONDS = 12.0
INSET_SECONDS = 2.0

# A host commonly uses the familiar form of a member's legal first name.  Keep
# this list explicit and conservative: an alias is useful only when the same
# evidence phrase also contains the member's exact/fuzzy surname, and it still
# authorizes screening rather than final identity acceptance.
FIRST_NAME_EQUIVALENTS = tuple(
    frozenset(group.split())
    for group in (
        "abraham abe",
        "alexander alex",
        "bernard bernie",
        "andrew andy",
        "anthony tony",
        "benjamin ben",
        "charles chuck",
        "chellie shelly",
        "christopher chris",
        "daniel dan",
        "david dave",
        "donald don",
        "derrick derek",
        "edward ed",
        "elijah eli",
        "elissa alyssa",
        "emilia amelia",
        "elizabeth liz beth betsy",
        "francis frank",
        "gerald jerry",
        "gregory greg",
        "james jim jimmy",
        "jacob jake",
        "janice jan",
        "jennifer jen jenny",
        "jefferson jeff",
        "jerrold jerry",
        "jesus chuy",
        "joseph joe",
        "kenneth ken",
        "katherine catherine kate kathy",
        "lawrence larry",
        "lori laurie",
        "michael mike",
        "margaret maggie meg",
        "nicholas nick",
        "patrick pat",
        "raymond ray",
        "rebecca becky",
        "richard rich rick",
        "robert rob bob bobby",
        "ronald ron",
        "stephen steven steve",
        "sharice charisse",
        "theodore ted",
        "thomas thom tom",
        "timothy tim",
        "william will bill",
    )
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def format_timed_words(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    bucket: list[dict[str, Any]] = []
    bucket_start = 0.0
    for row in rows:
        start = float(row["start_sec"])
        if bucket and start - bucket_start >= 5.0:
            lines.append(
                f"[{float(bucket[0]['start_sec']):.2f}-{float(bucket[-1]['end_sec']):.2f}] "
                + " ".join(str(item["word"]).strip() for item in bucket)
            )
            bucket = []
        if not bucket:
            bucket_start = start
        bucket.append(row)
    if bucket:
        lines.append(
            f"[{float(bucket[0]['start_sec']):.2f}-{float(bucket[-1]['end_sec']):.2f}] "
            + " ".join(str(item["word"]).strip() for item in bucket)
        )
    return "\n".join(lines)


def prompt(member: str, transcript: str) -> str:
    return f"""You are mechanically screening one congressional recording using only a local ASR transcript with timestamps.

TARGET PERSON: {member}

Do not use or infer from a video title, channel, date, office roster, topic, party, or policy position. MATCH requires a hard in-band signal in the transcript: a narrator/host/chair explicitly names the target immediately before the target speaks, or the target self-identifies by name. A generic state, chamber title, or 'the member' is insufficient.

Identify continuous spans where the named target alone is speaking. Exclude narrator/host introductions, interviewer questions, witness answers, cross-talk, and closing identifications. Start at the first target word and end at the last target word. Do not merge across another speaker. Return only spans at least 12 seconds long. If speaker turns cannot be separated confidently from the words and timing, return no spans.

For every returned span, the basis MUST include the exact first 4-10 words as: beginning with 'literal opening words'. This is a machine-checked boundary contract. Do not describe an opening without quoting it, and never use words spoken by the host/interviewer as the quoted opening.

Return one JSON object and nothing else:
{{"identity":"MATCH|MISMATCH|NEUTRAL","evidence":"exact literal phrase from transcript or NONE","evidence_start_sec":number|null,"evidence_end_sec":number|null,"target_spans":[{{"start_sec":number,"end_sec":number,"basis":"brief literal explanation"}}]}}

TIMED ASR TRANSCRIPT:
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
    if not isinstance(value, dict) or value.get("identity") not in {"MATCH", "MISMATCH", "NEUTRAL"}:
        raise ValueError("classifier JSON/identity is invalid")
    if not isinstance(value.get("target_spans"), list):
        raise ValueError("classifier target_spans is invalid")
    return value


def evidence_names_member(evidence: str, member: str) -> bool:
    evidence_tokens = normalized(evidence).split()
    member_tokens = normalized(member).split()
    if len(member_tokens) < 2 or not evidence_tokens:
        return False
    first = member_tokens[0]
    surname = member_tokens[-1]
    approved_firsts = {first}
    for group in FIRST_NAME_EQUIVALENTS:
        if first in group:
            approved_firsts.update(group)
    if not approved_firsts.intersection(evidence_tokens):
        return False
    return any(
        # ASR often renders an audible congressional surname phonetically
        # (Foushee -> Fushi, Luttrell -> Latterill).  The exact first name is
        # still required, and this is only a screening signal; the audio must
        # pass the later manual and acoustic gates.
        difflib.SequenceMatcher(None, surname, token, autojunk=False).ratio() >= 0.65
        for token in evidence_tokens
    )


def find_named_evidence_before_span(
    words: list[dict[str, Any]], member: str, before_sec: float
) -> dict[str, Any] | None:
    """Find the closest literal first+surname phrase before a target reply.

    The model sometimes identifies the correct target turns but copies a reply
    fragment into its ``evidence`` field.  Recover only a phrase that is
    physically present in the timestamped local ASR, ends before the first
    target span, and independently satisfies the same surname-bound name gate.
    """
    eligible = [row for row in words if float(row["end_sec"]) <= before_sec + 0.5]
    candidates: list[dict[str, Any]] = []
    for start in range(len(eligible)):
        for width in range(2, min(12, len(eligible) - start) + 1):
            chunk = eligible[start : start + width]
            phrase = " ".join(str(row.get("word") or "").strip() for row in chunk).strip()
            if evidence_names_member(phrase, member):
                candidates.append({
                    "evidence": phrase,
                    "start_sec": float(chunk[0]["start_sec"]),
                    "end_sec": float(chunk[-1]["end_sec"]),
                    "word_count": width,
                })
                break
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row["end_sec"], -row["word_count"]))


def snap_to_claimed_opening(
    basis: str, words: list[dict[str, Any]], snapped_start: float
) -> float | None:
    """Snap to a claimed literal opening, or reject when it is absent.

    The screening model can describe a target reply as "beginning with" a
    phrase while placing the boundary on the preceding interviewer.  This
    activates only for an explicit quoted opening claim in the model's own
    basis text.
    """
    claim = re.search(
        r"(?:beginning\s+with|from|literal\s+opening\s+words\s*:)\s+['\"]([^'\"]{3,120})['\"]",
        basis,
        re.I,
    )
    if not claim:
        return snapped_start
    nearby = [
        row
        for row in words
        if float(row["end_sec"]) > snapped_start - 15.0
        and float(row["start_sec"]) < snapped_start + 15.0
    ]
    tokens: list[str] = []
    token_starts: list[float] = []
    for row in nearby:
        for token in normalized(str(row.get("word") or "")).split():
            tokens.append(token)
            token_starts.append(float(row["start_sec"]))
    wanted = normalized(claim.group(1)).split()
    for index in range(0, len(tokens) - len(wanted) + 1):
        if tokens[index : index + len(wanted)] == wanted:
            return token_starts[index]
    return None


def snap_span(
    span: dict[str, Any], words: list[dict[str, Any]], duration: float
) -> dict[str, Any] | None:
    try:
        start = float(span["start_sec"])
        end = float(span["end_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= start < end <= duration + 0.5):
        return None
    starts = [float(row["start_sec"]) for row in words]
    ends = [float(row["end_sec"]) for row in words]
    snapped_start = min(starts, key=lambda value: abs(value - start))
    snapped_end = min(ends, key=lambda value: abs(value - end))
    basis = str(span.get("basis") or "")[:300]
    snapped_start = snap_to_claimed_opening(basis, words, snapped_start)
    if snapped_start is None:
        return None
    if snapped_end - snapped_start < MIN_SPAN_SECONDS:
        return None
    usable = max(0.0, snapped_end - snapped_start - 2 * INSET_SECONDS)
    return {
        "start_sec": round(snapped_start, 6),
        "end_sec": round(snapped_end, 6),
        "duration_sec": round(snapped_end - snapped_start, 6),
        "two_second_inset_exact_windows": int(usable // 8.0),
        "basis": basis,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    from anthropic import Anthropic  # type: ignore
    from fec.speech import source_media_recovery as SMR

    source_rows = read_jsonl(args.input)
    wanted = set(args.member)
    selected = [row for row in source_rows if not wanted or row["member"] in wanted]
    existing = {
        str(row["video_id"]): row
        for row in (read_jsonl(args.output) if args.output.exists() else [])
    }
    pending = [
        row for row in selected
        if args.refresh
        or str(row["video_id"]) not in existing
        or existing[str(row["video_id"])].get("status") == "CLASSIFIER_ERROR"
    ]
    key = args.key.read_text(encoding="utf-8").strip()

    def classify(row: dict[str, Any]) -> dict[str, Any]:
        base = {
            "schema": "local-youtube-turn-candidate-v1",
            "member": row["member"],
            "bioguide": row.get("bioguide"),
            "chamber": row.get("chamber"),
            "video_id": row["video_id"],
            "url": row.get("url"),
            "source_wav": row.get("source_wav"),
            "source_audio_sha256": row.get("source_audio_sha256"),
            "transcript": row.get("transcript"),
            "transcript_sha256": row.get("transcript_sha256"),
            "classifier_model": MODEL,
            "classified_at": utc_now(),
            "final_identity_acceptance": False,
        }
        try:
            words = read_jsonl(Path(str(row["transcript"])))
            transcript_text = " ".join(str(item["word"]).strip() for item in words)
            response = Anthropic(api_key=key).messages.create(
                model=MODEL,
                max_tokens=2400,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": prompt(str(row["member"]), format_timed_words(words)),
                }],
            )
            raw = "".join(
                block.text for block in response.content
                if getattr(block, "type", "") == "text"
            ).strip()
            parsed = parse_json(raw)
            duration = float(row.get("measured_duration") or (words[-1]["end_sec"] if words else 0.0))
            spans = [
                snapped for span in parsed["target_spans"]
                if (snapped := snap_span(span, words, duration)) is not None
            ]
            evidence = str(parsed.get("evidence") or "")
            evidence_start = parsed.get("evidence_start_sec")
            evidence_end = parsed.get("evidence_end_sec")
            evidence_literal = normalized(evidence) in normalized(transcript_text)
            hard_signal = evidence_literal and evidence_names_member(evidence, str(row["member"]))
            evidence_basis = "classifier_literal_name"
            if parsed["identity"] == "MATCH" and spans and not hard_signal:
                recovered = find_named_evidence_before_span(
                    words, str(row["member"]), float(spans[0]["start_sec"])
                )
                if recovered:
                    evidence = str(recovered["evidence"])
                    evidence_start = recovered["start_sec"]
                    evidence_end = recovered["end_sec"]
                    evidence_literal = True
                    hard_signal = True
                    evidence_basis = "deterministic_literal_pre_span_name_scan"
            candidate = parsed["identity"] == "MATCH" and hard_signal and bool(spans)
            return {
                **base,
                "status": "HARD_SIGNAL_TURN_CANDIDATE" if candidate else "SCREENED_OUT",
                "identity": parsed["identity"],
                "evidence": evidence,
                "evidence_start_sec": evidence_start,
                "evidence_end_sec": evidence_end,
                "evidence_basis": evidence_basis,
                "evidence_literal_in_asr": evidence_literal,
                "evidence_names_target": hard_signal,
                "target_spans": spans if candidate else [],
                "prospective_exact_windows": sum(
                    span["two_second_inset_exact_windows"] for span in spans
                ) if candidate else 0,
                "raw_response": raw,
                "next_required_gate": (
                    "exact-pcm-cut+voicing+half-consistency+all-vault-exclusion+manual-gold"
                    if candidate else None
                ),
            }
        except Exception as exc:
            return {
                **base,
                "status": "CLASSIFIER_ERROR",
                "error": f"{type(exc).__name__}: {exc}"[:1000],
                "raw_response": locals().get("raw", "")[:4000],
            }

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(classify, row): row for row in pending}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                failed_row = futures[future]
                result = {
                    "schema": "local-youtube-turn-candidate-v1",
                    "member": failed_row.get("member"),
                    "video_id": failed_row.get("video_id"),
                    "classifier_model": MODEL,
                    "classified_at": utc_now(),
                    "final_identity_acceptance": False,
                    "status": "CLASSIFIER_ERROR",
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "raw_response": "",
                }
            existing[str(result["video_id"])] = result
            completed += 1
            if completed % 5 == 0 or completed == len(pending):
                SMR.write_jsonl_atomic(
                    args.output,
                    sorted(existing.values(), key=lambda value: str(value["video_id"])),
                )
                print(f"CLASSIFY {completed}/{len(pending)}", flush=True)
    if not pending:
        SMR.write_jsonl_atomic(
            args.output, sorted(existing.values(), key=lambda value: str(value["video_id"]))
        )

    rows = list(existing.values())
    receipt = {
        "schema": "local-youtube-turn-candidate-receipt-v1",
        "status": "PASS_SCREENING_ONLY",
        "generated_at": utc_now(),
        "model": MODEL,
        "contract": {
            "literal_full_name_or_fuzzy_asr_name_required": True,
            "deterministic_literal_pre_span_name_recovery_allowed": True,
            "generic_state_or_title_is_identity_evidence": False,
            "two_second_span_inset_required": True,
            "screening_is_final_identity_acceptance": False,
        },
        "coverage": {
            "rows": len(rows),
            "hard_signal_turn_candidates": sum(
                row.get("status") == "HARD_SIGNAL_TURN_CANDIDATE" for row in rows
            ),
            "members_with_candidates": len({
                row["member"] for row in rows
                if row.get("status") == "HARD_SIGNAL_TURN_CANDIDATE"
            }),
            "prospective_exact_windows": sum(
                int(row.get("prospective_exact_windows") or 0) for row in rows
            ),
            "classifier_errors": sum(row.get("status") == "CLASSIFIER_ERROR" for row in rows),
        },
        "input": {"path": str(args.input.resolve()), "sha256": SMR.sha256_file(args.input)},
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
