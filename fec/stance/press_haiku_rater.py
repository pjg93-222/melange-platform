# -*- coding: utf-8 -*-
"""Haiku reader for press refresh rows (owner ruling D-219, Paul 2026-10-15).

press_stance_stack.py feeds a meta-model with a Fable reader's fields (target, alignment,
valence, confidence ...).  The refresh rows have no Fable reading, so this module supplies the
missing reader slot with Claude Haiku through the Claude CLI -- the subscription login, tool-free,
structured JSON, via ``classifier_runtime.classify_json`` -- never the API.

A Haiku reading reaches the stack only behind a KAPPA GATE.  Once per rater version the reader
scores a fixed gold sample (Paul's hand labels, the held-out ``split == test`` rows of Draft
Systems' ``cu_stance_label_set.csv``, n=87 -- the split the 2026-06-30 accuracy report used) and the
result is stored in ``data/stance/press_haiku_gate.json``.  The reader is admitted only if Cohen's
kappa >= KAPPA_MIN AND accuracy >= ACCURACY_MIN against those labels; otherwise the stack
bypasses itself for refresh rows (their Stage 5 label is used directly, stack_path='bypass').

  python press_haiku_rater.py status
  python press_haiku_rater.py gate [--budget N]   score the gold sample (resumable; spends CLI calls)
  python press_haiku_rater.py fill [--budget N]   rate unrated refresh rows (only if the gate passed)

The stack only READS the cache this writes; it never calls the CLI.
"""
from __future__ import annotations

import argparse
import collections
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import classifier_runtime
from paths import DRAFT, ROOT

# --- thresholds for Paul to adjust.  The stack re-applies the CURRENT values to the stored gate result. ---
KAPPA_MIN = 0.60
ACCURACY_MIN = 0.75
DEFAULT_BUDGET = 60                    # CLI calls per ``fill`` run
GATE_SAMPLE_N = 87                     # a changed gold sample must be re-ratified, not silently re-scored
GATE_SPLIT = "test"
LABELS = ("support", "oppose", "neutral")
RATER_MODEL = "haiku"
BODY_CHARS = 5000
MAX_CONSECUTIVE_ERRORS = 3
REFRESH_SOURCE = "PRESS_REFRESH_HOST_ARTICLE"

GOLD_PATH = os.path.join(DRAFT, "cu_stance_label_set.csv")
GATE_PATH = os.path.join(ROOT, "data", "stance", "press_haiku_gate.json")
GATE_CALLS_PATH = os.path.join(ROOT, "data", "stance", "press_haiku_gate_calls.jsonl")
CACHE_PATH = os.path.join(ROOT, "data", "stance", "press_haiku_cache.jsonl")

# The 'ftfix' press reader's prompt (press_reader_ftfix.py, full text, attribution rule fixed), verbatim.
SYSTEM = """You label press items for a credit-union (CU) legislative-stance model. You see the article's
FULL TEXT. Work through the factorization, in order:
1. TARGET — the specific bill/agency/rule/practice the item takes a position on. Constituent service,
   ceremony, grants, condolences, pure campaign content = no policy target = UNRELATED.
2. STANCE TOWARD TARGET — does the MEMBER support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors: expanding CFPB reach = -1, restraining/reforming it = +1; interchange caps / Credit Card
   Competition Act / card mandates = -1, blocking them = +1; measures favoring large banks over CUs or
   attacking the CU tax status = -1; reinforcing CU tax status, charter/field-of-membership expansion,
   MBL relief, reg-tailoring for small institutions = +1; fraud prevention, CU-sought data-privacy
   standards, CDFI funding, defense-CU access = +1. Generic anti-bank/populist rhetoric with no
   CU-relevant target = valence 0.
4. CU ALIGNMENT — stance(member toward target) x valence(target for CUs). Supporting an anti-CU target
   = oppose. Opposing an anti-CU target = support. Neutral stance or neutral target = neutral.
5. ATTRIBUTION — whose position does this item evidence?
   * member-own: the member's own press release / their quoted words.
   * reported-action: a third party reporting an ACTION the member took (sponsored, cosponsored, voted,
     met with advocates, withdrew an amendment). Alignment follows the member's ACTION, not the
     article's tone.
   * third-party-voice: the stance language belongs to someone other than the member. IMPORTANT: this
     does NOT default to unrelated. Credit-union trade press covers members BECAUSE they acted on CU
     issues — coverage describing a member's bill, vote, letter, or meeting evidences that member's
     alignment even when the quotes are the trade group's. Infer the member's position from the actions
     and framing described. Use unrelated ONLY if the text genuinely evidences nothing about this
     member's position on any CU-relevant target.
Confidence: high = text clearly establishes actor, target, and direction; medium = probable but one
element inferred; low = insufficient (prefer neutral/unrelated + low over a guessed direction).
Keep rationale to one sentence."""

SCHEMA = {"type": "object",
          "properties": {"target": {"type": "string"},
                         "stance_toward_target": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                         "target_cu_valence": {"type": "integer", "enum": [1, 0, -1]},
                         "cu_alignment": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                         "attribution": {"type": "string", "enum": ["member-own", "reported-action", "third-party-voice"]},
                         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                         "rationale": {"type": "string"}},
          "required": ["target", "stance_toward_target", "target_cu_valence", "cu_alignment",
                       "attribution", "confidence", "rationale"], "additionalProperties": False}

# Any change to the model, prompt, schema or input window is a NEW rater and needs its own gate.
RATER_VERSION = "haiku-ftfix-v1-" + hashlib.sha256(json.dumps(
    [RATER_MODEL, SYSTEM, SCHEMA, BODY_CHARS], sort_keys=True).encode("utf-8")).hexdigest()[:12]

_QUOTA = re.compile(r'spend limit|usage limit|rate limit|"api_error_status"\s*:\s*429', re.IGNORECASE)


class ClassifierQuotaExhausted(RuntimeError):
    """The CLI reported a rate or spend limit; every later call would fail the same way, so the run stops."""


def url_key(url):
    """The Fable cache's key: md5 of the raw url (press_stance_stack.featurize)."""
    return hashlib.md5((url or "").strip().encode()).hexdigest()


def build_user(row, body):
    ul = (row.get("url") or "").lower()
    domain = ("americascreditunions.org (the CU trade association itself)" if "americascreditunions" in ul
              else ("the member's own congressional website" if (".house.gov" in ul or ".senate.gov" in ul)
                    else "a third-party outlet"))
    return ("Member: %s (%s, source: %s)\nDate: %s\nHeadline: %s\nFull text: %s" % (
        row.get("member") or "", row.get("party") or "", domain, row.get("date") or "",
        row.get("title") or "", (body or row.get("title") or "")[:BODY_CHARS])
        ) + "\n(Keep rationale under 12 words.)"


def rate(user, classify=None):
    """One structured reading, validated.  A rate/spend limit raises ClassifierQuotaExhausted."""
    classify = classify or classifier_runtime.classify_json
    try:
        out, _meta = classify(SYSTEM, user, SCHEMA, model=RATER_MODEL)
    except RuntimeError as exc:
        if _QUOTA.search(str(exc)):
            raise ClassifierQuotaExhausted(str(exc)) from exc
        raise
    properties = SCHEMA["properties"]
    for name in SCHEMA["required"]:
        if name not in out:
            raise ValueError("rater output lacks %r" % name)
        allowed = properties[name].get("enum")
        if allowed is not None and out[name] not in allowed:
            raise ValueError("rater output %s=%r is outside %r" % (name, out[name], allowed))
    return {name: out[name] for name in SCHEMA["required"]}


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\r\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def _read_jsonl(path):
    rows = []
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue                                    # a torn last line from a crash
    return rows


def _append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# --- the gate ------------------------------------------------------------------------------------------
def gold_sample(path=GOLD_PATH):
    """The fixed held-out sample: ``split == test`` rows with a valid human label, in file order."""
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle)
                if (row.get("split") or "").strip().lower() == GATE_SPLIT
                and (row.get("human_label") or "").strip().lower() in LABELS]
    return [{**row, "human_label": row["human_label"].strip().lower()} for row in rows]


def sample_sha256(sample):
    return hashlib.sha256("\n".join("%s\t%s" % (row.get("url") or "", row["human_label"])
                                    for row in sample).encode("utf-8")).hexdigest()


def cohen_kappa(gold, predicted, labels=LABELS):
    n = len(gold)
    if n == 0:
        return 0.0
    observed = sum(g == p for g, p in zip(gold, predicted)) / n
    gold_counts, pred_counts = collections.Counter(gold), collections.Counter(predicted)
    expected = sum(gold_counts[label] * pred_counts[label] for label in labels) / (n * n)
    return 1.0 if expected == 1.0 else (observed - expected) / (1.0 - expected)


def _gate_label(out):
    """The gold has three labels, so an 'unrelated' reading is scored as neutral."""
    return "neutral" if out["cu_alignment"] == "unrelated" else out["cu_alignment"]


def run_gate(*, classify=None, budget=None, gold_path=GOLD_PATH, gate_path=GATE_PATH,
             calls_path=GATE_CALLS_PATH, now=None):
    """Score the gold sample once per rater version.  Returns the stored gate record, or a status dict.

    Resumable: every reading is appended to ``calls_path`` as it lands, so a quota stop or a crash costs
    nothing already paid for.  ``budget`` caps NEW calls this run (default: the whole sample); the gate
    record is written only when every sample row has a reading.
    """
    sample = gold_sample(gold_path)
    if len(sample) != GATE_SAMPLE_N:
        raise RuntimeError("gold sample has %d rows, expected %d; re-ratify GATE_SAMPLE_N" % (len(sample), GATE_SAMPLE_N))
    sha = sample_sha256(sample)
    if os.path.isfile(gate_path):
        stored = json.loads(io.open(gate_path, encoding="utf-8").read())
        if stored.get("rater_version") == RATER_VERSION and stored.get("sample_sha256") == sha:
            return {**stored, "status": "already_gated"}
    readings = {row["k"]: row["out"] for row in _read_jsonl(calls_path)
                if row.get("v") == RATER_VERSION and row.get("sample_sha256") == sha}
    budget = len(sample) if budget is None else budget
    made, stopped = 0, None
    for row in sample:
        key = url_key(row.get("url"))
        if key in readings:
            continue
        if made >= budget:
            stopped = "budget"
            break
        try:
            readings[key] = rate(build_user(row, row.get("text_snippet") or ""), classify)
        except ClassifierQuotaExhausted as exc:
            stopped = "quota_exhausted: %s" % exc
            break
        except (RuntimeError, ValueError) as exc:
            stopped = "error: %s" % exc
            break
        made += 1
        _append_jsonl(calls_path, {"v": RATER_VERSION, "sample_sha256": sha, "k": key, "out": readings[key]})
    done = sum(url_key(row.get("url")) in readings for row in sample)
    if done < len(sample):
        return {"status": "incomplete", "stopped": stopped, "readings": done, "n": len(sample), "calls_made": made}
    gold = [row["human_label"] for row in sample]
    predicted = [_gate_label(readings[url_key(row.get("url"))]) for row in sample]
    accuracy = sum(g == p for g, p in zip(gold, predicted)) / len(gold)
    kappa = cohen_kappa(gold, predicted)
    record = {
        "schema": "press-haiku-gate-v1", "rater_version": RATER_VERSION, "model": RATER_MODEL,
        "gold_source": "cu_stance_label_set.csv split=%s human_label" % GATE_SPLIT,
        "sample_sha256": sha, "n": len(sample), "accuracy": accuracy, "kappa": kappa,
        "kappa_min": KAPPA_MIN, "accuracy_min": ACCURACY_MIN,
        "passed": kappa >= KAPPA_MIN and accuracy >= ACCURACY_MIN,
        "confusion": {"%s>%s" % pair: count for pair, count in sorted(collections.Counter(zip(gold, predicted)).items())},
        "scored_at": (now or datetime.now(timezone.utc)).isoformat(),
    }
    _atomic_json(gate_path, record)
    return {**record, "status": "scored", "calls_made": made}


def gate_status(gate_path=GATE_PATH, gold_path=GOLD_PATH):
    """``(admitted, detail)``.  The stored accuracy and kappa are judged against the CURRENT thresholds."""
    if not os.path.isfile(gate_path):
        return False, "no gate result for this rater (run the gate)"
    try:
        stored = json.loads(io.open(gate_path, encoding="utf-8").read())
        current = sample_sha256(gold_sample(gold_path))
    except (OSError, ValueError, KeyError) as exc:
        return False, "gate result or gold sample unreadable: %s" % exc
    if stored.get("rater_version") != RATER_VERSION:
        return False, "gate was run for rater %s, current is %s" % (stored.get("rater_version"), RATER_VERSION)
    if stored.get("sample_sha256") != current:
        return False, "gold sample changed since the gate ran"
    try:
        kappa, accuracy = float(stored["kappa"]), float(stored["accuracy"])
    except (KeyError, TypeError, ValueError):
        return False, "gate result lacks kappa/accuracy"
    detail = "kappa %.3f (min %.2f), accuracy %.3f (min %.2f), n=%s" % (
        kappa, KAPPA_MIN, accuracy, ACCURACY_MIN, stored.get("n"))
    return (kappa >= KAPPA_MIN and accuracy >= ACCURACY_MIN), detail


# --- the slot the stack reads --------------------------------------------------------------------------
def load_slot(cache_path=CACHE_PATH):
    """{md5(url): reading} for the CURRENT rater version only."""
    return {row["k"]: row["out"] for row in _read_jsonl(cache_path) if row.get("v") == RATER_VERSION}


def admitted_slot(cache_path=CACHE_PATH, gate_path=GATE_PATH, gold_path=GOLD_PATH):
    """The readings the stack may use: all of them if the gate passed, none otherwise."""
    admitted, detail = gate_status(gate_path, gold_path)
    return (load_slot(cache_path) if admitted else {}), admitted, detail


# --- filling the slot ----------------------------------------------------------------------------------
def run_fill(tone_rows, fulltext, fable_keys, *, classify=None, budget=DEFAULT_BUDGET, cache_path=CACHE_PATH,
             gate_path=GATE_PATH, gold_path=GOLD_PATH):
    """Rate refresh rows that have neither a Fable reading nor a Haiku one, at most ``budget`` CLI calls.

    ``fulltext`` maps canonical_url(url, keep_fragment=True) to the body.  Nothing is called unless the gate passed.
    """
    admitted, detail = gate_status(gate_path, gold_path)
    if not admitted:
        return {"status": "gate_not_passed", "detail": detail, "attempted": 0, "rated": 0}
    from press_stream import canonical_url
    have = set(load_slot(cache_path)) | set(fable_keys)
    todo = [row for row in tone_rows if (row.get("source") or "").strip().upper() == REFRESH_SOURCE
            and (row.get("url") or "").strip() and url_key(row.get("url")) not in have]
    attempted = rated = errors = consecutive = 0
    stopped = None
    for row in todo:
        if attempted >= budget:
            stopped = "budget"
            break
        attempted += 1
        try:
            out = rate(build_user(row, fulltext.get(canonical_url(row.get("url"), keep_fragment=True), "")), classify)
        except ClassifierQuotaExhausted:
            stopped = "quota_exhausted"
            break
        except (RuntimeError, ValueError):
            errors += 1
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                stopped = "errors"
                break
            continue
        consecutive = 0
        rated += 1
        _append_jsonl(cache_path, {"v": RATER_VERSION, "k": url_key(row.get("url")), "out": out})
    return {"status": "ok", "detail": detail, "attempted": attempted, "rated": rated, "errors": errors,
            "unrated_remaining": len(todo) - rated, "stopped": stopped}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "gate", "fill"))
    parser.add_argument("--budget", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "status":
        admitted, detail = gate_status()
        print("rater %s: %s -- %s" % (RATER_VERSION, "ADMITTED" if admitted else "NOT admitted", detail))
        return 0
    if args.command == "gate":
        result = run_gate(budget=args.budget)
        print(json.dumps({key: value for key, value in result.items() if key != "confusion"}, indent=2, sort_keys=True))
        return 0 if result.get("status") in ("scored", "already_gated") else 1
    from press_stream import load_fulltext, load_tone_rows
    fable = {row["k"] for row in _read_jsonl(os.path.join(ROOT, "data", "stance", "api_press_cache_fable.jsonl"))}
    result = run_fill(load_tone_rows(), load_fulltext(), fable,
                      budget=DEFAULT_BUDGET if args.budget is None else args.budget)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
