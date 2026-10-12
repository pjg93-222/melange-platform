#!/usr/bin/env python3
"""Attach deterministic, evidence-bounded one-on-one meeting playbooks.

The active three-paragraph profiles retain their reviewed prose.  This adds a
separate four-part operational layer for every roster member so a lobbyist can
quickly see how to open, what to bring, how to pace the exchange, and how to
close.  It uses only fields already admitted to the narrative evidence ledger.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "member_narratives_v2.json"
EVIDENCE = ROOT / "data/members/member_narrative_evidence_v4.json"
RECEIPT = ROOT / "data/members/member_narratives_v2_semantic_receipt.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def pct(measures: dict, key: str) -> float | None:
    try:
        return float((measures.get(key) or {}).get("pct"))
    except (TypeError, ValueError):
        return None


INFERRED_FINDING_PHASES = {
    "individual_conversation_before_decision": "open",
    "familiarity_and_trust_in_working_relationships": "open",
    "personal_checkin_before_policy": "open",
    "principle_first_then_operational_boundary": "open",
    "broad_prompt_produces_multi_minute_answer": "run_the_room",
    "observed_multi_minute_answer": "run_the_room",
    "observed_extended_answer": "run_the_room",
    "observed_subminute_answer": "run_the_room",
}


def phased_findings(record: dict, phase: str, *, role_conditioned: bool = False) -> list[dict]:
    """Return admitted findings for one practical meeting phase.

    New reviewed findings carry ``meeting_phase`` explicitly.  The inference
    map preserves compatibility with the small set promoted before that field
    became mandatory.
    """
    longform = ((record.get("speech_profile") or {}).get("longform_interview_evidence") or {})
    if role_conditioned:
        findings = longform.get("role_conditioned_reviewed_semantic_findings") or []
    else:
        findings = longform.get("baseline_reviewed_semantic_findings")
        if findings is None:
            findings = longform.get("reviewed_semantic_findings") or []
    output = []
    seen = set()
    for finding in findings:
        finding_phase = finding.get("meeting_phase") or INFERRED_FINDING_PHASES.get(
            str(finding.get("kind") or "")
        )
        implication = str(finding.get("meeting_implication") or "").strip()
        key = implication.casefold()
        if finding_phase == phase and implication and key not in seen:
            output.append(finding)
            seen.add(key)
    return output


def implication_text(findings: list[dict], *, role_conditioned: bool = False) -> str:
    text = " ".join(str(row.get("meeting_implication") or "").strip() for row in findings).strip()
    if text and role_conditioned:
        text += " This is current-role evidence; treat it as current practice until pre-role audio confirms the baseline."
    return text


def opening(record: dict) -> tuple[str, str]:
    speech = record.get("speech_profile") or {}
    longform = speech.get("longform_interview_evidence") or {}
    all_findings = longform.get("reviewed_semantic_findings") or []
    baseline_findings = longform.get("baseline_reviewed_semantic_findings")
    if baseline_findings is None:  # ledgers built before source-era separation
        baseline_findings = all_findings
    role_conditioned_findings = longform.get("role_conditioned_reviewed_semantic_findings") or []
    finding_kinds = {str(row.get("kind")) for row in baseline_findings}
    role_conditioned_kinds = {str(row.get("kind")) for row in role_conditioned_findings}
    relationship_sequence = {
        "individual_conversation_before_decision",
        "familiarity_and_trust_in_working_relationships",
        "personal_checkin_before_policy",
    }
    if finding_kinds & relationship_sequence:
        return (
            'Do not start cold. Use the first minute for a genuine personal or shared-context exchange, let the member '
            'answer, then pivot: "We are asking for [action] by [date]." Follow with one fact and one human consequence.',
            "reviewed_longform_relationship_sequence",
        )
    if role_conditioned_kinds & relationship_sequence:
        return (
            'Do not start cold. Use the first minute for a genuine personal or shared-context exchange, let the member '
            'answer, then pivot: "We are asking for [action] by [date]." Follow with one fact and one human consequence. '
            "This sequence is observed in current-role interviews; "
            "treat it as current-practice evidence until pre-role audio confirms the baseline.",
            "role_conditioned_longform_relationship_sequence",
        )
    if "principle_first_then_operational_boundary" in finding_kinds:
        return (
            'Lead with: "We are asking for [action] by [date]." Connect it to one governing principle, '
            "then state the operational limit or implementation choice. Do not leave the ask at shared values.",
            "reviewed_prerole_principle_then_boundary",
        )
    baseline_open = phased_findings(record, "open")
    if baseline_open:
        return (
            implication_text(baseline_open) + ' Then say: "We are asking for [action] by [date]."',
            "reviewed_longform_open:" + ",".join(str(row.get("kind")) for row in baseline_open),
        )
    conditioned_open = phased_findings(record, "open", role_conditioned=True)
    if conditioned_open:
        return (
            implication_text(conditioned_open, role_conditioned=True)
            + ' Then say: "We are asking for [action] by [date]."',
            "role_conditioned_longform_open:" + ",".join(
                str(row.get("kind")) for row in conditioned_open
            ),
        )
    appeals = speech.get("motivating_appeals") or {}
    dimensions = " ".join(appeals.get("supported_dimensions") or []).casefold()
    if "communicative" in dimensions or "community" in dimensions or "human-impact" in dimensions:
        return (
            'Lead with: "[Constituency] is experiencing [consequence]; we are asking for [action] by [date]."',
            "community_or_human_impact_frame",
        )
    if "assertive" in dimensions or "outcome-led" in dimensions or "immediate" in dimensions:
        return (
            'Lead with: "We are asking for [action] by [date] to produce [result]." Then add one human consequence.',
            "outcome_frame",
        )
    return (
        'Lead with: "We are asking for [action] by [date]." Follow with one fact and one human consequence.',
        "neutral_fact_plus_human_frame",
    )


def leave_behind(record: dict) -> tuple[str, str]:
    cooperation = ((record.get("behavior") or {}).get("cross_party_cooperation") or {})
    cooperation_pct = cooperation.get("pct")
    base = "Use one page: requested decision, deadline, affected people, and implementation path."
    if cooperation_pct is not None and float(cooperation_pct) >= 0.70:
        text, rule = base + " Name a credible cross-party partner if one is already part of the plan.", "high_cross_party_share"
    elif cooperation_pct is not None and float(cooperation_pct) <= 0.30:
        text, rule = base + " Lead with the merits of the action rather than a generic bipartisan appeal.", "low_cross_party_share"
    else:
        text, rule = base + " Keep source detail behind the page and ready for follow-up.", "neutral_or_unavailable_cross_party_share"
    baseline_bring = phased_findings(record, "bring")
    conditioned_bring = phased_findings(record, "bring", role_conditioned=True)
    if baseline_bring:
        text += " " + implication_text(baseline_bring)
        rule += "+reviewed_longform_bring:" + ",".join(str(row.get("kind")) for row in baseline_bring)
    elif conditioned_bring:
        text += " " + implication_text(conditioned_bring, role_conditioned=True)
        rule += "+role_conditioned_longform_bring:" + ",".join(
            str(row.get("kind")) for row in conditioned_bring
        )
    return text, rule


def room_pacing(record: dict) -> tuple[str, list[str]]:
    speech = record.get("speech_profile") or {}
    interaction = speech.get("interaction_habits") or {}
    measures = interaction.get("measures") or {}
    mean_turn = pct(measures, "wpt_mean")
    long_tail = pct(measures, "wpt_p90")
    floor = pct(measures, "gini")
    burst = pct(measures, "burst")
    cues: list[str] = []
    rules: list[str] = []
    reviewed_turn_pacing = False

    skip_direct_implication_kinds = {
        "broad_prompt_produces_multi_minute_answer",
        "observed_multi_minute_answer",
        "observed_extended_answer",
        "observed_subminute_answer",
    }
    baseline_room = [
        row for row in phased_findings(record, "run_the_room")
        if row.get("kind") not in skip_direct_implication_kinds
    ]
    conditioned_room = [
        row for row in phased_findings(record, "run_the_room", role_conditioned=True)
        if row.get("kind") not in skip_direct_implication_kinds
    ]
    if baseline_room:
        cues.append(implication_text(baseline_room))
        rules.extend("reviewed_longform:" + str(row.get("kind")) for row in baseline_room)
    elif conditioned_room:
        cues.append(implication_text(conditioned_room, role_conditioned=True))
        rules.extend("role_conditioned_longform:" + str(row.get("kind")) for row in conditioned_room)

    longform = speech.get("longform_interview_evidence") or {}
    baseline_kinds = {
        str(row.get("kind"))
        for row in (longform.get("baseline_reviewed_semantic_findings") or [])
    }
    if "broad_prompt_produces_multi_minute_answer" in baseline_kinds:
        cues.append(
            "Ask one narrow question first; if time is limited, avoid a broad or compound opening that can hand over "
            "the floor for several minutes."
        )
        rules.append("reviewed_prerole_broad_prompt_long_answer")
        reviewed_turn_pacing = True

    reviewed_turns = [
        row for row in (longform.get("full_turns") or [])
        if row.get("semantic_review_qualified") is True
    ]
    if record.get("leadership_audio_source_policy"):
        reviewed_turns = [
            row for row in reviewed_turns
            if row.get("source_era") == "PRE_CURRENT_LEADERSHIP_ROLE"
            and row.get("baseline_semantic_eligible") is True
        ]
    durations = [
        float(row.get("duration_seconds") or 0.0)
        for row in reviewed_turns
        if float(row.get("duration_seconds") or 0.0) > 0.0
    ]
    if durations and "broad_prompt_produces_multi_minute_answer" not in baseline_kinds:
        longest = max(durations)
        reviewed_turn_pacing = True
        if longest >= 120.0:
            cues.append(
                f"A reviewed long-form answer ran {longest / 60.0:.1f} minutes; protect time for a complete answer "
                "and use a narrow opening when the meeting needs a decision."
            )
            rules.append("reviewed_longform_multi_minute_turn")
        elif longest >= 60.0:
            cues.append(
                f"A reviewed long-form answer ran {longest:.0f} seconds; ask one question at a time and leave room "
                "for the answer to develop."
            )
            rules.append("reviewed_longform_one_minute_turn")
        else:
            cues.append(
                f"The available reviewed long-form answer ran {longest:.0f} seconds. Treat that as one observed "
                "exchange, not proof that every answer will be brief."
            )
            rules.append("reviewed_longform_subminute_turn")

    if mean_turn is not None and mean_turn <= 0.30 and long_tail is not None and long_tail >= 0.70:
        cues.append("Expect mostly short answers with an occasional longer one; leave enough time for the longer answer to develop.")
        rules.append("short_mean_long_tail")
    elif mean_turn is not None and mean_turn >= 0.70:
        if not reviewed_turn_pacing:
            cues.append("Give the first answer room to run and ask one question at a time.")
            rules.append("long_mean_turn")
    elif mean_turn is not None and mean_turn <= 0.30:
        cues.append("Expect shorter answers; keep follow-ups short and do not treat brevity as agreement or closure.")
        rules.append("short_mean_turn")
    elif floor is not None and floor >= 0.70:
        cues.append("Let the member establish the sequence, then return to the requested decision.")
        rules.append("high_floor_concentration")
    elif floor is not None and floor <= 0.30:
        cues.append("Ask a direct question and leave protected response time rather than filling the opening.")
        rules.append("low_floor_concentration")
    elif not reviewed_turn_pacing:
        cues.append("Ask one question at a time, pause, and summarize the answer before moving to the next point.")
        rules.append("neutral_interaction_fallback")

    if burst is not None and burst >= 0.70:
        cues.append("Be ready for quick contested back-and-forth; answer the point directly instead of stacking explanations.")
        rules.append("high_contested_exchange")
    elif burst is not None and burst <= 0.30:
        cues.append("Do not force a rapid back-and-forth; let each answer finish before narrowing the question.")
        rules.append("low_contested_exchange")

    if speech.get("repeated_defensive_reactions"):
        cues.append("If a premise is challenged, answer it once with the strongest fact and let the response finish.")
        rules.append("repeated_floor_defense")
    return " ".join(cues), rules


def close(record: dict) -> tuple[str, str]:
    note = str((record.get("receptivity") or {}).get("pressure_note") or "").casefold()
    ending = "Close by confirming the office owner, the next deliverable, and the date."
    magnitude = re.search(r"(\d+)%", note)
    if magnitude and int(magnitude.group(1)) <= 10:
        text, rule = ending, "near_typical_pressure_return"
    elif "less than" in note:
        text, rule = "Do not compensate with repeated pressure. " + ending, "below_typical_pressure_return"
    elif "more than" in note:
        text, rule = "A firm, specific follow-up is reasonable. " + ending, "above_typical_pressure_return"
    else:
        text, rule = ending, "neutral_or_mixed_pressure_return"
    baseline_close = phased_findings(record, "close")
    conditioned_close = phased_findings(record, "close", role_conditioned=True)
    if baseline_close:
        text = implication_text(baseline_close) + " " + text
        rule += "+reviewed_longform_close:" + ",".join(str(row.get("kind")) for row in baseline_close)
    elif conditioned_close:
        text = implication_text(conditioned_close, role_conditioned=True) + " " + text
        rule += "+role_conditioned_longform_close:" + ",".join(
            str(row.get("kind")) for row in conditioned_close
        )
    return text, rule


def observed_longform(record: dict) -> dict | None:
    speech = record.get("speech_profile") or {}
    longform = speech.get("longform_interview_evidence") or {}
    turns = [
        row for row in (longform.get("full_turns") or [])
        if row.get("semantic_review_qualified") is True
    ]
    if record.get("leadership_audio_source_policy"):
        turns = [
            row for row in turns
            if row.get("source_era") == "PRE_CURRENT_LEADERSHIP_ROLE"
            and row.get("baseline_semantic_eligible") is True
        ]
    if not turns:
        return None
    turn = max(turns, key=lambda row: float(row.get("duration_seconds") or 0.0))
    seconds = float(turn.get("duration_seconds") or 0.0)
    if seconds >= 120.0:
        duration = f"{seconds / 60.0:.1f} minutes"
    else:
        duration = f"{seconds:.0f} seconds"
    findings = [
        {
            "finding": str(row.get("finding") or "").strip(),
            "meeting_implication": str(row.get("meeting_implication") or "").strip(),
            "scope": str(row.get("scope") or "").strip(),
        }
        for row in (turn.get("semantic_findings") or [])
        if str(row.get("finding") or "").strip()
    ]
    return {
        "source_id": turn.get("source_id"),
        "title": turn.get("title"),
        "channel": turn.get("channel"),
        "url": turn.get("url"),
        "recorded_at": turn.get("recorded_at"),
        "published": turn.get("published"),
        "source_era": turn.get("source_era"),
        "turn_id": turn.get("turn_id"),
        "duration_seconds": round(seconds, 3),
        "summary": f"One complete reviewed answer ran {duration} from floor-taking to the next speaker handoff.",
        "findings": findings,
        "scope": "one reviewed public long-form turn; not a prediction that every private meeting will follow it",
    }


def build(record: dict) -> dict:
    open_text, open_rule = opening(record)
    bring_text, bring_rule = leave_behind(record)
    room_text, room_rules = room_pacing(record)
    close_text, close_rule = close(record)
    return {
        "open": open_text,
        "bring": bring_text,
        "run_the_room": room_text,
        "close": close_text,
        "observed_longform": observed_longform(record),
        "derivation": {
            "open": open_rule,
            "bring": bring_rule,
            "run_the_room": room_rules,
            "close": close_rule,
        },
        "scope": "public-professional preparation; evidence-bounded guidance, not a prediction of private behavior",
    }


def main() -> int:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8-sig"))
    evidence_doc = json.loads(EVIDENCE.read_text(encoding="utf-8-sig"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8-sig"))
    members = artifact.get("members") or {}
    evidence = evidence_doc.get("members") or {}
    if set(members) != set(evidence):
        raise SystemExit("active profile and evidence rosters differ")

    for name in sorted(members):
        members[name]["meeting_playbook"] = build(evidence[name])

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    artifact.setdefault("provenance", {})["meeting_playbooks_20260903"] = {
        "completed_utc": now,
        "members": len(members),
        "evidence_artifact": str(EVIDENCE.relative_to(ROOT)).replace("\\", "/"),
        "evidence_sha256": sha256(EVIDENCE),
        "method": "deterministic translation of source-era-separated reviewed full-turn semantics plus admitted appeal, interaction, cross-party-share, and pressure-return fields",
        "contract": "No private-state inference; neutral fact-plus-human, one-page, pause, owner, and date fallback when evidence does not select a style.",
    }
    write_json_atomic(ARTIFACT, artifact)
    active_hash = sha256(ARTIFACT)

    receipt["created_utc"] = now
    receipt["candidate_sha256"] = active_hash
    receipt["active_artifact_sha256"] = active_hash
    receipt.setdefault("counts", {})["meeting_playbooks"] = len(members)
    receipt["meeting_playbooks"] = artifact["provenance"]["meeting_playbooks_20260903"]
    write_json_atomic(RECEIPT, receipt)
    print(json.dumps({"meeting_playbooks": len(members), "active_artifact_sha256": active_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
