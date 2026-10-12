# -*- coding: utf-8 -*-
"""Transcript-to-audio turn alignment for the speaker-identity layer.

audio-identity design 2026-07-16 (user-directed build).  Research-only: the argparse main writes a
.candidate artifact consumed by NOTHING until ratified.

Purpose: given the ordered turn texts of one hearing (hearings_seq.db), an Aeneas sync map, and the
word-level output of a LOCAL ASR pass over that hearing's audio, locate each transcript turn inside
the audio timeline and independently grade how much of it the ASR recovered. Aeneas supplies the
paper-faithful MFCC/dynamic-time-warp boundary proposal; ASR supplies a separate content-recovery
cross-check. Downstream identity checks (verification, attribution) only trust a turn's [start, end]
window when its word_match clears
ALIGN_MIN_TURN_WORD_MATCH, and only trust the hearing at all when the overall matched fraction
clears ALIGN_MIN_HEARING_WORD_MATCH -- both constants live in audio_rules.py, never here.

Method
------
1. Token normalization: lowercase, keep only [a-z0-9'] runs, strip leading/trailing apostrophes.
   Numbers are deliberately NOT spelled out to words (no "2026" <-> "twenty twenty six" bridging):
   a numeric transcript token that the ASR wrote as words simply counts as unmatched and lowers
   word_match.  Conservative by design -- normalization can only fail toward exclusion.
2. The concatenated turn-token stream is globally aligned against the ASR token stream with a
   monotonic Needleman-Wunsch dynamic program (match = +1, mismatch = -1, gap = -1), vectorized
   per row with numpy (the left-gap recurrence is closed via a running max of score + column).
   Monotonicity is structural: each ASR token is consumed at most once and in order, so matched
   ASR indices never regress across turns and turn windows cannot overlap.  (Sole caveat: a single
   ASR "word" that normalizes to multiple tokens spanning a turn boundary can make two adjacent
   turns share that word's timestamps; synthetic and CHRG-scale inputs do not exhibit this.)
3. Preferred path: Aeneas fragment [begin, end] values become the boundaries only when fragment
   count, normalized text, finite timestamps, and monotonic non-overlap all validate exactly.
   The independently aligned ASR stream supplies word_match. Any Aeneas structural mismatch fails
   the entire hearing closed; ASR never silently substitutes its own boundaries on that path.
4. Fallback/test path without a sync map: [start, end] = the first/last matched ASR word inside the
   turn's token span. word_match = exactly-matched token fraction of the turn's tokens.
   Hearing level: hearing_word_match = matched fraction over all turn tokens; hearing_usable =
   hearing_word_match >= ALIGN_MIN_HEARING_WORD_MATCH.

ASR source: asr_words_from_whisper() runs faster-whisper (lazily imported) from an EXPLICIT local
model directory with greedy decoding pinned (temperature=0.0, beam_size=1, best_of=1) so reruns are
reproducible.  It hard-refuses (BackendRefused) when the directory is missing or incomplete and
never downloads weights -- model acquisition is a separate, user-approved step.

Aeneas is invoked EXTERNALLY with a pinned local toolchain; this module never imports it or permits
runtime downloads. Its standard fragment JSON is ingested directly by align_turns_aeneas().
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from audio_rules import ALIGN_MIN_HEARING_WORD_MATCH, ALIGN_MIN_TURN_WORD_MATCH
from members.b5_audio_embed import BackendRefused

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_WHISPER_REQUIRED = ("model.bin",)


def normalize_tokens(text):
    """Lowercase word tokens: [a-z0-9'] runs, outer apostrophes stripped, empties dropped."""
    out = []
    for raw in _TOKEN_RE.findall(str(text or "").lower()):
        tok = raw.strip("'")
        if tok:
            out.append(tok)
    return out


def _nw_matched_indices(ref_ids, hyp_ids):
    """Monotonic Needleman-Wunsch (match +1, mismatch -1, gap -1) over integer token ids.

    Returns match_at: list len(ref_ids) whose entry is the hyp position exactly matched on the
    chosen global path, or None.  Deterministic: fixed scoring, fixed traceback preference
    (diagonal, then left-gap, then up-gap).
    """
    n, m = len(ref_ids), len(hyp_ids)
    match_at = [None] * n
    if n == 0 or m == 0:
        return match_at
    hyp = np.asarray(hyp_ids, dtype=np.int64)
    idx = np.arange(m + 1, dtype=np.int64)
    score = np.empty((n + 1, m + 1), dtype=np.int64)
    score[0] = -idx
    tmp = np.empty(m + 1, dtype=np.int64)
    for i in range(1, n + 1):
        srow = np.where(hyp == ref_ids[i - 1], 1, -1)
        tmp[0] = score[i - 1, 0] - 1
        np.maximum(score[i - 1, :-1] + srow, score[i - 1, 1:] - 1, out=tmp[1:])
        # Close the left-gap recurrence H[i,j] = max_{k<=j} (tmp[k] - (j - k)) in one accumulate.
        score[i] = np.maximum.accumulate(tmp + idx) - idx
    i, j = n, m
    while i > 0 and j > 0:
        diag = 1 if ref_ids[i - 1] == hyp_ids[j - 1] else -1
        if score[i, j] == score[i - 1, j - 1] + diag:
            if diag == 1:
                match_at[i - 1] = j - 1
            i -= 1
            j -= 1
        elif score[i, j] == score[i, j - 1] - 1:
            j -= 1
        else:
            i -= 1
    return match_at


def align_turns(turn_texts, asr_words):
    """Align ordered turn texts against an ASR word stream.

    turn_texts: ordered list of turn strings (hearings_seq.db turn text).
    asr_words:  [{"word": str, "start": float, "end": float}] from a local ASR pass.

    Returns {"turns": [alignment turn records], "hearing_word_match": float,
    "hearing_usable": bool}.  Each turn record follows the frozen contract:
    {"turn_index": int, "start": float, "end": float, "word_match": float, "usable": bool}
    (start/end are None when no token of the turn matched -- such a turn is never usable).
    """
    turn_tokens = [normalize_tokens(text) for text in turn_texts]
    ref_tokens = [tok for toks in turn_tokens for tok in toks]

    hyp_tokens = []
    hyp_word_index = []  # asr_words index behind each hyp token (a word may yield >1 token)
    for wi, word in enumerate(asr_words):
        for tok in normalize_tokens(word.get("word", "")):
            hyp_tokens.append(tok)
            hyp_word_index.append(wi)

    vocab = {}
    for tok in ref_tokens:
        vocab.setdefault(tok, len(vocab))
    ref_ids = [vocab[tok] for tok in ref_tokens]
    hyp_ids = [vocab.get(tok, -1) for tok in hyp_tokens]  # -1 never matches any ref id

    match_at = _nw_matched_indices(ref_ids, hyp_ids)

    records = []
    pos = 0
    total_matched = 0
    for turn_index, toks in enumerate(turn_tokens):
        span = match_at[pos:pos + len(toks)]
        pos += len(toks)
        matched_words = [hyp_word_index[h] for h in span if h is not None]
        total_matched += len(matched_words)
        word_match = (len(matched_words) / float(len(toks))) if toks else 0.0
        if matched_words:
            start = float(asr_words[matched_words[0]]["start"])
            end = float(asr_words[matched_words[-1]]["end"])
        else:
            start = end = None
        records.append({
            "turn_index": turn_index,
            "start": start,
            "end": end,
            "word_match": word_match,
            "usable": bool(matched_words) and word_match >= ALIGN_MIN_TURN_WORD_MATCH,
        })

    total_tokens = len(ref_tokens)
    hearing_word_match = (total_matched / float(total_tokens)) if total_tokens else 0.0
    return {
        "turns": records,
        "hearing_word_match": hearing_word_match,
        "hearing_usable": total_tokens > 0 and hearing_word_match >= ALIGN_MIN_HEARING_WORD_MATCH,
    }


def align_turns_aeneas(turn_texts, syncmap, asr_words):
    """Use Aeneas fragments as boundaries and local ASR only as a word-recovery cross-check.

    ``syncmap`` is the standard Aeneas JSON object with ``fragments`` containing ``begin``, ``end``,
    and ``lines``. Fragment count and normalized text must match the transcript turns exactly.
    Any malformed, regressing, overlapping, or mismatched fragment fails the whole hearing closed.
    """
    crosscheck = align_turns(turn_texts, asr_words)
    errors = []
    fragments = syncmap.get("fragments") if isinstance(syncmap, dict) else None
    if not isinstance(fragments, list):
        errors.append("aeneas fragments must be a list")
        fragments = []
    if len(fragments) != len(turn_texts):
        errors.append("aeneas fragment count %d != turn count %d" %
                      (len(fragments), len(turn_texts)))

    boundaries = []
    previous_end = None
    for index, turn_text in enumerate(turn_texts):
        if index >= len(fragments) or not isinstance(fragments[index], dict):
            boundaries.append((None, None))
            if index < len(fragments):
                errors.append("aeneas fragment %d is not an object" % index)
            continue
        fragment = fragments[index]
        lines = fragment.get("lines")
        if isinstance(lines, list):
            fragment_text = " ".join(str(line) for line in lines)
        else:
            fragment_text = str(lines or "")
        if normalize_tokens(fragment_text) != normalize_tokens(turn_text):
            errors.append("aeneas fragment %d text does not match transcript turn" % index)
        try:
            begin = float(fragment.get("begin"))
            end = float(fragment.get("end"))
        except (TypeError, ValueError):
            errors.append("aeneas fragment %d has non-numeric timestamps" % index)
            boundaries.append((None, None))
            continue
        if not (math.isfinite(begin) and math.isfinite(end) and 0.0 <= begin <= end):
            errors.append("aeneas fragment %d has invalid timestamps" % index)
        if previous_end is not None and begin < previous_end:
            errors.append("aeneas fragment %d overlaps or regresses" % index)
        previous_end = end
        boundaries.append((begin, end))

    if errors:
        records = []
        for index, turn in enumerate(crosscheck["turns"]):
            records.append({
                "turn_index": index,
                "start": None,
                "end": None,
                "word_match": turn["word_match"],
                "usable": False,
            })
    else:
        records = []
        for index, (turn, boundary) in enumerate(zip(crosscheck["turns"], boundaries)):
            records.append({
                "turn_index": index,
                "start": boundary[0],
                "end": boundary[1],
                "word_match": turn["word_match"],
                "usable": turn["usable"],
            })
    return {
        "turns": records,
        "hearing_word_match": crosscheck["hearing_word_match"],
        "hearing_usable": crosscheck["hearing_usable"] and not errors,
        "boundary_source": "aeneas_mfcc_dtw",
        "crosscheck_source": "local_asr_word_match",
        "validation_errors": errors,
    }


def asr_words_from_whisper(wav_path, model_dir):
    """Word-timestamped ASR via faster-whisper from an explicit LOCAL model directory.

    Refuses (BackendRefused) before any import when model_dir is missing or incomplete; never
    downloads.  Greedy decoding is pinned (temperature=0.0, beam_size=1, best_of=1) so two runs
    over the same audio and weights produce the same word stream.
    """
    if not model_dir:
        raise BackendRefused(
            "whisper: model_dir is required; this helper never downloads models at runtime")
    model_dir = os.path.abspath(model_dir)
    if not os.path.isdir(model_dir):
        raise BackendRefused(
            "whisper: model dir %s does not exist; acquire weights via the user-approved "
            "download step first" % model_dir)
    missing = [name for name in _WHISPER_REQUIRED
               if not os.path.isfile(os.path.join(model_dir, name))]
    if missing:
        raise BackendRefused(
            "whisper: model dir %s is missing %s; acquire weights via the user-approved "
            "download step first" % (model_dir, ", ".join(missing)))
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise BackendRefused("whisper: faster-whisper not installed") from exc
    model = WhisperModel(model_dir, device="cpu", compute_type="int8", local_files_only=True)
    segments, _info = model.transcribe(
        wav_path, temperature=0.0, beam_size=1, best_of=1,
        word_timestamps=True, condition_on_previous_text=False)
    words = []
    for segment in segments:
        for word in (segment.words or []):
            words.append({"word": word.word, "start": float(word.start), "end": float(word.end)})
    return words


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Align hearing turn texts to a local ASR word stream (research candidate).")
    parser.add_argument("--turns-json", required=True,
                        help="JSON file: ordered list of turn text strings")
    parser.add_argument("--asr-json", required=True,
                        help="JSON file: list of {word,start,end} from a local ASR pass")
    parser.add_argument("--aeneas-json",
                        help="optional standard Aeneas sync-map JSON; when present its fragments "
                             "are the only boundary source and ASR is an independent cross-check")
    parser.add_argument("--out", required=True,
                        help="explicit output path; must end in .json.candidate")
    args = parser.parse_args(argv)
    if not args.out.endswith(".json.candidate"):
        print("REFUSE: --out must end in .json.candidate (research artifact, consumed by nothing)")
        raise SystemExit(2)
    with open(args.turns_json, encoding="utf-8-sig") as handle:  # -sig: Windows tools add BOMs
        turn_texts = json.load(handle)
    with open(args.asr_json, encoding="utf-8-sig") as handle:
        asr_words = json.load(handle)
    if args.aeneas_json:
        with open(args.aeneas_json, encoding="utf-8-sig") as handle:
            syncmap = json.load(handle)
        result = align_turns_aeneas(turn_texts, syncmap, asr_words)
        alignment_method = "aeneas_mfcc_dtw_with_local_asr_crosscheck"
        aeneas_sha256 = _sha256_file(args.aeneas_json)
    else:
        result = align_turns(turn_texts, asr_words)
        alignment_method = "local_asr_monotonic_fallback"
        aeneas_sha256 = None
    artifact = {
        "schema": "b5-audio-align-v1",
        "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
        "alignment_method": alignment_method,
        "aeneas_syncmap_sha256": aeneas_sha256,
        "turns": result["turns"],
        "hearing_word_match": result["hearing_word_match"],
        "hearing_usable": result["hearing_usable"],
        "validation_errors": result.get("validation_errors", []),
        "thresholds": {
            "align_min_turn_word_match": ALIGN_MIN_TURN_WORD_MATCH,
            "align_min_hearing_word_match": ALIGN_MIN_HEARING_WORD_MATCH,
        },
    }
    from moneyball_core import dump_atomic
    dump_atomic(artifact, args.out, indent=1)
    usable = sum(1 for rec in result["turns"] if rec["usable"])
    print("wrote %s (turns=%d usable=%d hearing_word_match=%.3f hearing_usable=%s)" % (
        args.out, len(result["turns"]), usable, result["hearing_word_match"],
        result["hearing_usable"]))


if __name__ == "__main__":
    main()
