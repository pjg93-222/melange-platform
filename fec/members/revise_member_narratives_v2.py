# -*- coding: utf-8 -*-
"""Audit and revise the active member approach briefs through Anthropic Messages.

The workflow has three commands:

``plan``
    Read-only/no-key inventory of the installed artifact and evidence ledger.
``audit``
    Run an independent semantic grounding review over every installed brief.
``generate``
    Regenerate the union of known cross-party-claim briefs and audit failures.

An API key is read only from ``ANTHROPIC_API_KEY`` or an explicit
``--api-key-file``.  It is never accepted on the command line, printed, or
written to logs.  Generated JSON artifacts are ignored by the repository.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:      # run as a script: make fec.roster importable
    sys.path.insert(0, str(ROOT))
from fec import roster  # noqa: E402
EVIDENCE_PATH = ROOT / "data" / "members" / "member_narrative_evidence_v4.json"
ARTIFACT_PATH = ROOT / "member_narratives_v2.json"
AUDIT_PATH = ROOT / "data" / "members" / "member_narratives_v2_semantic_audit.json"
REVISIONS_PATH = ROOT / "data" / "members" / "member_narratives_v2_revisions.json"
WORK_DIR = ROOT / "data" / "members" / "member_narratives_v2_work"
WRITER_PROMPT_PATH = Path(__file__).with_name("member_narratives_v2_prompt.md")
REVIEW_PROMPT_PATH = Path(__file__).with_name("member_narratives_v2_review_prompt.md")
API_URL = "https://api.anthropic.com/v1/messages"
HEADINGS = ("Overall personality.", "Manner of speech & landmines.", "Persuasive approach.")

CROSS_PARTY_MARKERS = (
    "across the aisle", "party lines", "cross party", "cross-party", "bipartisan",
    "bipartisanship", "reaches across", "reaching across", "reached across", "reach across",
    "other side of the aisle", "aisle", "partisan", "party line", "across party",
    "both parties", "other party", "own party", "opposite party", "opposing party",
    "the other side", "cosponsor", "co-sponsor", "outside their party", "outside his party",
    "outside her party", "party divide", "across party lines",
)
TARGET_MARKERS = (
    "across the aisle", "party lines", "cross party", "cross-party",
    "bipartisan", "reaches across", "other side of the aisle",
)

BANNED_PRIVATE_STATE = (
    "unflappable", "even-keeled", "level-headed", "unruffled", "imperturbable",
    "emotionally stable", "emotional stability", "volatile", "erratic", "neurotic",
    "anxious", "anxiety", "unstable", "stable", "stability", "calm", "calmer",
    "calmly", "calmness", "composed", "composure", "steady", "steadiness",
    "reactive", "reactivity", "emotionally reactive", "emotional reactivity",
    "high-strung", "high strung", "even-tempered", "even tempered",
    "emotional resilience", "low-strain",
)
BANNED_UNSUPPORTED = (
    "seeks the camera", "chases the camera", "camera-seeking", "doing the work quietly",
    "solo operator", "travels in groups", "trigger topic", "media-practised",
    "media-practiced", "polished answers", "vocal strain",
    "verified-solo audio", "rapid-fire pace",
)
BANNED_JARGON = (
    "the ledger", "z-score", "z score", "percentile", "pressure_mult", "raw_text",
    "cross_setting", "headline_interviews", "affective content", "issue breadth",
    "volume dynamics", "pitch variation", "split-half", "qualifying turns",
)

BAND_DIRECTIONS = {
    "among the highest in the chamber": "far more cross-party than almost every covered colleague",
    "well above most": "clearly more cross-party than most covered colleagues, but not an extreme superlative",
    "above most": "somewhat more cross-party than most covered colleagues; only a mild claim",
    "middling": "ordinary among covered colleagues; normally omit this rather than call it a strength or weakness",
    "below most": "somewhat less cross-party than most covered colleagues; only a mild claim",
    "well below most": "clearly less cross-party than most covered colleagues, but not an extreme superlative",
    "among the lowest in the chamber": "far less cross-party than almost every covered colleague",
}


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_atomic(document, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def trim_nulls(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            child = trim_nulls(child)
            if child not in (None, {}, [], ""):
                result[key] = child
        return result
    if isinstance(value, list):
        return [trim_nulls(child) for child in value]
    return value


def body_text(member_record) -> str:
    return "\n".join(str(member_record.get(key) or "") for key in ("p1", "p2", "p3"))


def headed_text(member_record) -> str:
    return "\n".join("**%s** %s" % (heading, str(member_record.get(key) or "").strip())
                     for heading, key in zip(HEADINGS, ("p1", "p2", "p3")))


def cross_party_hits(text: str):
    lowered = text.lower()
    spans = []
    for marker in CROSS_PARTY_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index < 0:
                break
            spans.append((index, index + len(marker), marker))
            start = index + 1
    spans.sort()
    merged = []
    for start, end, marker in spans:
        if merged and start < merged[-1][1]:
            old_start, old_end, old_marker = merged[-1]
            merged[-1] = (old_start, max(old_end, end), old_marker if len(old_marker) >= len(marker) else marker)
        else:
            merged.append((start, end, marker))
    return merged


def known_cross_party_targets(artifact) -> set[str]:
    return {
        name for name, row in (artifact.get("members") or {}).items()
        if any(marker in body_text(row).lower() for marker in TARGET_MARKERS)
    }


def coop_band(record):
    return (record.get("behavior") or {}).get("cross_party_cooperation")


def canonical_trait_sentence(record, trait: str):
    band = ((((record.get("traits") or {}).get(trait) or {}).get("band")) or {})
    label = band.get("label")
    if not label:
        return None
    if trait == "Neuroticism":
        return (
            "Broad Neuroticism is %s in the expert-perceived reading; this public trait estimate "
            "stands on its own and does not establish a private emotional state." % label
        )
    return (
        "Broad %s is %s in the expert-perceived reading; this public trait estimate stands on "
        "its own and does not predict observed conversational behavior." % (trait, label)
    )


def directive(record) -> str:
    cooperation = coop_band(record)
    broad_a = (((record.get("traits") or {}).get("Agreeableness") or {}).get("band") or {})
    qualified_a = ((((record.get("qualified_reads") or {}).get("agreeableness_low_antagonism") or {}).get("band")) or {})
    hearing_a = (((record.get("hearing_antagonism") or {}).get("band")) or {})
    pieces = [str(record.get("construct_rule") or "")]
    if cooperation:
        pieces.append(
            "Cross-party cooperation is PRESENT: band=%s. Required direction: %s. "
            "This is share-based tracked behavior, not Agreeableness or bill volume."
            % (cooperation["label"], BAND_DIRECTIONS[cooperation["label"]])
        )
    else:
        pieces.append(
            "Cross-party cooperation is ABSENT. The brief must contain no reference to the aisle, "
            "bipartisanship, party lines, cosponsoring, either party, or cross-party colleagues."
        )
    pieces.append(
        "Independent A-related readings: broad Agreeableness=%s; qualified interview-language "
        "low-antagonism=%s; hearing antagonism=%s. Preserve all scopes and do not force agreement."
        % (broad_a.get("label", "absent"), qualified_a.get("label", "absent"), hearing_a.get("label", "absent"))
    )
    return "\n".join(pieces)


def mechanical_issues(text: str, name: str, record) -> list[str]:
    issues = []
    paragraphs = [paragraph.strip() for paragraph in text.strip().splitlines() if paragraph.strip()]
    if len(paragraphs) != 3:
        issues.append("exactly three non-empty paragraphs are required; got %d" % len(paragraphs))
    for index, heading in enumerate(HEADINGS):
        expected = "**%s**" % heading
        if index >= len(paragraphs) or not paragraphs[index].startswith(expected):
            issues.append("paragraph %d must begin with %s" % (index + 1, expected))
    if "\u2014" in text or "\u2013" in text:
        issues.append("em/en dash is forbidden")
    lowered = text.lower()
    for term in BANNED_PRIVATE_STATE:
        if re.search(r"\b%s\b" % re.escape(term), lowered):
            issues.append("unsupported private-state term: %s" % term)
    for term in BANNED_UNSUPPORTED:
        if term in lowered:
            issues.append("known unsupported inference: %s" % term)
    for term in BANNED_JARGON:
        if term in lowered:
            issues.append("measurement/report jargon: %s" % term)
    speech_profile = record.get("speech_profile") or {}
    if not speech_profile.get("repeated_defensive_reactions") and re.search(
            r"\bdefen(?:s|c|d)\w*\b|\bpush\s*back\b|\bpushback\b|\bpushes\s+back\b|\bpushing\s+back\b",
            lowered):
        issues.append("defensive language appears without the repeated-defensive evidence gate")
    if not (speech_profile.get("supported_topic_associations") or []) and re.search(
            r"\btrigger(?:s|ed|ing)?\b|\btopic associations?\b|\btopic sensitivity\b|\bsensitive (?:area|subject|topic)\b|\bpressure points?\b|\blandmine topics?\b|\btopic landmines?\b|\bhot-button topics?\b|\b(?:specific|particular) (?:subject|topic)\b|\btopic-specific\b|\boff-limits\b|\b(?:subject|topic) to avoid\b|\bno topic\b|\bno link to any .*topic\b",
            lowered):
        issues.append("topic/trigger language appears without a supported topic association")
    if coop_band(record) is None and cross_party_hits(text):
        issues.append("cross-party subject appears although the measure is absent")
    return sorted(set(issues))


def parse_json_object(text: str):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("pass"), bool) or not isinstance(value.get("issues"), list):
        raise ValueError("review response does not satisfy the required object shape")
    if not all(isinstance(issue, dict) for issue in value["issues"]):
        raise ValueError("review response issues must be JSON objects")
    if value.get("construct_checks") is not None and not isinstance(value.get("construct_checks"), dict):
        raise ValueError("review response construct_checks must be a JSON object")
    value["issues"] = [
        issue for issue in value["issues"]
        if not str(issue.get("repair") or "").strip().lower().startswith(
            ("no repair needed", "n/a", "retract")
        )
    ]
    if value["pass"] and value["issues"]:
        raise ValueError("review response contradicts itself: pass=true with issues")
    if not value["pass"] and not value["issues"]:
        raise ValueError("review response contradicts itself: pass=false with no actionable issues")
    return value


class ApiClient:
    def __init__(self, key: str, model: str, api_log: Path):
        self.key = key
        self.model = model
        self.api_log = api_log
        self.api_log.parent.mkdir(parents=True, exist_ok=True)
        self.log_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.cooldown_lock = threading.Lock()
        self.cooldown_until = 0.0
        self.stats = collections.Counter()

    def _wait(self):
        while True:
            with self.cooldown_lock:
                delay = self.cooldown_until - time.time()
            if delay <= 0:
                return
            time.sleep(min(delay, 5.0))

    def _cooldown(self, seconds):
        with self.cooldown_lock:
            self.cooldown_until = max(self.cooldown_until, time.time() + seconds)

    def call(self, *, system: str, user: str, purpose: str, member: str, round_number: int, max_tokens: int):
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        delay = 2.0
        last_error = None
        for attempt in range(8):
            self._wait()
            request = urllib.request.Request(API_URL, data=payload, method="POST")
            request.add_header("content-type", "application/json")
            request.add_header("x-api-key", self.key)
            request.add_header("anthropic-version", "2023-06-01")
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    document = json.loads(response.read().decode("utf-8"))
                text = "".join(block.get("text", "") for block in document.get("content") or [] if block.get("type") == "text")
                if not text.strip():
                    raise RuntimeError("API response contained no text block")
                usage = document.get("usage") or {}
                with self.stats_lock:
                    self.stats["calls"] += 1
                    self.stats[purpose + "_calls"] += 1
                    self.stats["input_tokens"] += usage.get("input_tokens") or 0
                    self.stats["output_tokens"] += usage.get("output_tokens") or 0
                    self.stats["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens") or 0
                    self.stats["cache_read_input_tokens"] += usage.get("cache_read_input_tokens") or 0
                log_entry = {
                    "member": member, "purpose": purpose, "round": round_number,
                    "model": self.model, "usage": usage, "stop_reason": document.get("stop_reason"),
                }
                with self.log_lock:
                    with self.api_log.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                return text.strip()
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:500]
                last_error = "HTTP %s: %s" % (error.code, detail)
                if error.code not in (429, 500, 502, 503, 529):
                    raise RuntimeError(last_error)
                retry_after = error.headers.get("retry-after") if error.headers else None
                try:
                    wait = float(retry_after) if retry_after else delay
                except ValueError:
                    wait = delay
                wait += random.uniform(0, min(5.0, wait))
                if error.code in (429, 529):
                    self._cooldown(wait)
                with self.stats_lock:
                    self.stats["retries"] += 1
                time.sleep(wait)
            except Exception as error:  # noqa: BLE001
                last_error = repr(error)
                with self.stats_lock:
                    self.stats["retries"] += 1
                time.sleep(delay + random.uniform(0, 2.0))
            delay = min(delay * 2, 60.0)
        raise RuntimeError("API failed after retries: %s" % last_error)


def read_key(args) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if not args.api_key_file:
        raise SystemExit("API key needed: set ANTHROPIC_API_KEY or pass --api-key-file PATH")
    text = args.api_key_file.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("API key file is empty")
    if text.startswith("{"):
        document = json.loads(text)
        candidates = [
            document.get("ANTHROPIC_API_KEY"), document.get("anthropic_api_key"),
            ((document.get("anthropic") or {}).get("key") if isinstance(document.get("anthropic"), dict) else None),
            ((((document.get("anthropic") or {}).get("keys") or [None])[0]) if isinstance(document.get("anthropic"), dict) else None),
        ]
        key = next((str(value).strip() for value in candidates if value), "")
    else:
        key = text
    if not key:
        raise SystemExit("could not find an Anthropic API key in the supplied file")
    return key


def review_draft(client, review_system, name, record, draft, round_number=1):
    request = (
        "MEMBER-SPECIFIC DIRECTIVE\n%s\n\nEVIDENCE LEDGER\n%s\n\nDRAFT TO AUDIT\n%s"
        % (directive(record), json.dumps(trim_nulls(record), ensure_ascii=False, indent=1), draft)
    )
    errors = []
    for attempt in range(2):
        raw = client.call(system=review_system, user=request, purpose="review", member=name,
                          round_number=round_number * 10 + attempt, max_tokens=4000)
        try:
            return parse_json_object(raw)
        except (json.JSONDecodeError, ValueError) as error:
            errors.append(str(error))
            request += "\n\nYour prior response was not valid contract JSON. Return only the required JSON object."
    return {"pass": False, "issues": [{"quote": "", "kind": "review_contract_failure",
                                         "reason": "; ".join(errors), "repair": "rerun semantic review"}],
            "construct_checks": {}}


def append_jsonl(path: Path, record, lock):
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_latest_jsonl(path: Path):
    records = {}
    if not path.is_file():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                records[record["name"]] = record
    return records


def audit_all(args, client, evidence, artifact, review_system):
    progress_path = WORK_DIR / "audit_progress.jsonl"
    progress_lock = threading.Lock()
    existing = read_latest_jsonl(progress_path) if args.resume else {}
    names = sorted(evidence)
    def needs_review(name):
        if name not in existing:
            return True
        issues = ((existing[name].get("semantic_review") or {}).get("issues") or [])
        return any(isinstance(issue, dict) and issue.get("kind") == "review_contract_failure" for issue in issues)

    todo = [name for name in names if needs_review(name)]
    reusable = len(names) - len(todo)
    print("semantic audit: %d total, %d reusable, %d to review" % (len(names), reusable, len(todo)), flush=True)

    def work(name):
        draft = headed_text(artifact[name])
        mechanical = mechanical_issues(draft, name, evidence[name])
        semantic = review_draft(client, review_system, name, evidence[name], draft)
        result = {
            "name": name,
            "pass": not mechanical and semantic.get("pass") is True,
            "mechanical_issues": mechanical,
            "semantic_review": semantic,
        }
        append_jsonl(progress_path, result, progress_lock)
        return result

    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(work, name): name for name in todo}
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            print("[audit %3d/%3d] %-4s %s" % (completed, len(todo), "ok" if result["pass"] else "FAIL", result["name"]), flush=True)

    records = read_latest_jsonl(progress_path)
    failed = sorted(name for name, result in records.items() if not result.get("pass"))
    document = {
        "schema": "member-narratives-v2-semantic-audit-v1",
        "status": "PASS" if len(records) == roster.count() and not failed else "REPAIRS_REQUIRED",
        "model": client.model,
        "evidence_basis_sha256": load(EVIDENCE_PATH).get("basis_sha256"),
        "count": len(records),
        "pass_count": len(records) - len(failed),
        "fail_count": len(failed),
        "failed_members": failed,
        "members": records,
    }
    dump_atomic(document, args.audit_out)
    print("audit result: %d pass, %d require repair -> %s" % (document["pass_count"], document["fail_count"], args.audit_out))
    return document


def strip_heading(paragraph: str, heading: str):
    expected = "**%s**" % heading
    paragraph = paragraph.strip()
    if not paragraph.startswith(expected):
        raise ValueError("missing expected heading " + expected)
    return paragraph[len(expected):].strip()


def repair_feedback(attempt):
    if not isinstance(attempt, dict) or not attempt.get("draft"):
        return ""
    defects = list(attempt.get("mechanical_issues") or [])
    semantic = attempt.get("semantic_review") or {}
    if semantic.get("pass") is False:
        defects.extend(
            "%s: %s" % (issue.get("quote") or issue.get("kind"), issue.get("reason"))
            for issue in semantic.get("issues") or []
        )
    if not defects:
        return ""
    return (
        "\n\nPRIOR DRAFT TO REPAIR\n"
        + attempt["draft"]
        + "\n\nPRIOR DRAFT DEFECTS\nRevise that draft conservatively, fix every item, "
          "and return the complete three-paragraph replacement:\n"
        + "\n".join("- " + defect for defect in defects)
    )


def generate_one(args, client, writer_system, review_system, name, record, prior_result=None):
    ledger = json.dumps(trim_nulls(record), ensure_ascii=False, indent=1)
    prior_attempts = (prior_result or {}).get("attempts") or []
    feedback = repair_feedback(prior_attempts[-1]) if prior_attempts else ""
    attempts = []
    for round_number in range(1, args.max_rounds + 1):
        request = (
            "Write the three-paragraph approach brief for the member identified below. Use singular they/them pronouns. "
            "The name is supplied only to identify the row and must not appear in the prose.\n\n"
            "MEMBER: %s (%s)\n\nMEMBER-SPECIFIC DIRECTIVE\n%s\n\nEVIDENCE LEDGER\n%s%s"
            % (name, record.get("chamber") or "", directive(record), ledger, feedback)
        )
        draft = client.call(system=writer_system, user=request, purpose="write", member=name,
                            round_number=round_number, max_tokens=1600)
        mechanical = mechanical_issues(draft, name, record)
        semantic = None
        if not mechanical:
            semantic = review_draft(client, review_system, name, record, draft, round_number)
        attempt = {"round": round_number, "draft": draft, "mechanical_issues": mechanical,
                   "semantic_review": semantic}
        attempts.append(attempt)
        if not mechanical and semantic and semantic.get("pass") is True:
            paragraphs = [paragraph.strip() for paragraph in draft.splitlines() if paragraph.strip()]
            return {
                "name": name, "ok": True, "rounds": round_number,
                "p1": strip_heading(paragraphs[0], HEADINGS[0]),
                "p2": strip_heading(paragraphs[1], HEADINGS[1]),
                "p3": strip_heading(paragraphs[2], HEADINGS[2]),
                "cross_party_covered": coop_band(record) is not None,
                "cross_party_label": (coop_band(record) or {}).get("label"),
                "semantic_review": semantic, "attempts": attempts,
            }
        feedback = repair_feedback(attempt)
    return {"name": name, "ok": False, "rounds": args.max_rounds, "attempts": attempts,
            "defects": ["exhausted regeneration rounds"]}


def generate_repairs(args, client, evidence, artifact, audit_doc, writer_system, review_system):
    known_targets = known_cross_party_targets({"members": artifact})
    audit_failures = set(audit_doc.get("failed_members") or [])
    targets = sorted(known_targets | audit_failures)
    progress_path = WORK_DIR / "generation_progress.jsonl"
    progress_lock = threading.Lock()
    existing = read_latest_jsonl(progress_path) if args.resume else {}
    completed_ok = {
        name for name, row in existing.items()
        if row.get("ok") and name in evidence
        and not mechanical_issues(headed_text(row), name, evidence[name])
    }
    todo = [name for name in targets if name not in completed_ok]
    print("regeneration: %d targets = %d known cross-party + %d additional audit failures; %d to run" %
          (len(targets), len(known_targets), len(audit_failures - known_targets), len(todo)), flush=True)

    def work(name):
        try:
            result = generate_one(
                args, client, writer_system, review_system, name, evidence[name], existing.get(name)
            )
        except Exception as error:  # noqa: BLE001
            result = {"name": name, "ok": False, "error": repr(error)}
        append_jsonl(progress_path, result, progress_lock)
        return result

    completed = 0
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(work, name): name for name in todo}
            for future in as_completed(futures):
                result = future.result()
                completed += 1
                print("[write %3d/%3d] %-4s %s rounds=%s" %
                      (completed, len(todo), "ok" if result.get("ok") else "FAIL", result["name"], result.get("rounds", "-")), flush=True)

    latest = read_latest_jsonl(progress_path)
    members = {name: latest[name] for name in targets if name in latest}
    failed = sorted(name for name in targets if not (members.get(name) or {}).get("ok"))
    document = {
        "schema": "member-narratives-v2-revisions-v1",
        "status": "PASS" if len(members) == len(targets) and not failed else "INCOMPLETE",
        "model": client.model,
        "target_rule": "union of all 2026-09-01 cross-party-claim briefs and all full-corpus semantic audit failures",
        "known_cross_party_target_count": len(known_targets),
        "additional_semantic_failure_count": len(audit_failures - known_targets),
        "count": len(members), "ok_count": len(members) - len(failed), "failed_members": failed,
        "members": members,
    }
    dump_atomic(document, args.revisions_out)
    print("generation result: %d/%d accepted -> %s" % (document["ok_count"], len(targets), args.revisions_out))
    return document


def validate_inputs(evidence_doc, artifact_doc):
    evidence = evidence_doc.get("members") or {}
    artifact = artifact_doc.get("members") or {}
    if evidence_doc.get("schema") != "member-narrative-evidence-v4" or evidence_doc.get("status") != "ACTIVE_FAIL_CLOSED_INPUT":
        raise SystemExit("evidence ledger is not active/fail-closed; rebuild it first")
    if artifact_doc.get("schema") != "member-narratives-v2-api" or artifact_doc.get("status") != "ACTIVE_API_GENERATED_USER_RATIFIED":
        raise SystemExit("installed narrative artifact has an unexpected provenance label")
    if artifact_doc.get("basis_sha256") != evidence_doc.get("basis_sha256"):
        raise SystemExit("artifact/evidence basis hash mismatch")
    if set(evidence) != set(artifact) or len(evidence) != roster.count():
        raise SystemExit("artifact/evidence member keys do not match exactly")
    return evidence, artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "audit", "generate", "run"))
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--audit-out", type=Path, default=AUDIT_PATH)
    parser.add_argument("--revisions-out", type=Path, default=REVISIONS_PATH)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    evidence_doc = load(EVIDENCE_PATH)
    artifact_doc = load(ARTIFACT_PATH)
    evidence, artifact = validate_inputs(evidence_doc, artifact_doc)
    targets = known_cross_party_targets(artifact_doc)
    covered_targets = sum(coop_band(evidence[name]) is not None for name in targets)
    if len(targets) != 268 or covered_targets != 143:
        raise SystemExit("known target gate failed: targets=%d covered=%d (expected 268/143)" % (len(targets), covered_targets))
    if args.command == "plan":
        print("members=%d known_cross_party_targets=268 covered_targets=143 uncovered_targets=125" % roster.count())
        print("audit_calls=%d; generation target is 268 plus any additional audit failures" % roster.count())
        print("maximum generation calls=%d writer + %d reviewer at %d rounds; actual stops on first accepted round" %
              (roster.count() * args.max_rounds, roster.count() * args.max_rounds, args.max_rounds))
        return 0

    key = read_key(args)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    client = ApiClient(key, args.model, WORK_DIR / "api_usage.jsonl")
    writer_system = WRITER_PROMPT_PATH.read_text(encoding="utf-8")
    review_system = REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
    if args.command in ("audit", "run"):
        audit_doc = audit_all(args, client, evidence, artifact, review_system)
    else:
        audit_doc = load(args.audit_out)
        if audit_doc.get("count") != roster.count():
            raise SystemExit("semantic audit is incomplete; run the audit command first")
    if args.command in ("generate", "run"):
        generate_repairs(args, client, evidence, artifact, audit_doc, writer_system, review_system)
    print("API usage: " + json.dumps(dict(client.stats), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
