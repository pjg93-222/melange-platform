#!/usr/bin/env python3
"""Activate the 2026-09-03 evidence-bound targeted profile refresh."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from fec.members import revise_member_narratives_v2 as validator


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "member_narratives_v2.json"
EVIDENCE = ROOT / "data/members/member_narrative_evidence_v4.json"
INTERVIEWS = ROOT / "data/speech/member_interview_descriptive_v1.json"
INTERVIEW_RECEIPT = ROOT / "data/speech/member_interview_descriptive_v1.receipt.json"
LONG_TURNS = ROOT / "data/speech/member_interview_long_turns_v1.jsonl"
LONG_TURN_RECEIPT = ROOT / "data/speech/member_interview_long_turns_v1.receipt.json"
LEADERSHIP_LONG_TURNS = ROOT / "data/speech/member_leadership_prerole_long_turns_v1.jsonl"
LEADERSHIP_LONG_TURN_RECEIPT = ROOT / "data/speech/member_leadership_prerole_long_turns_v1.receipt.json"
RECEIPT = ROOT / "data/members/member_narratives_v2_semantic_receipt.json"

PROFILES = {
    "Mike Johnson": {
        "p1": (
            "Mike Johnson's measures describe high public presence around a comparatively settled and narrow "
            "range of frames. His expert-perceived Extraversion is around the top fifth, while Openness is "
            "around the bottom twelve percent and a separate public-language read of policy-subject range is around "
            "the bottom ten percent. Those are public, professional estimates; they do not prove that he is "
            "incurious in private. Expert-perceived Conscientiousness is around the bottom fifth, while the "
            "direct legislative execution measures available here are mostly near the middle, so do not turn "
            "the broad trait estimate into a claim that his office will not follow through. Agreeableness and "
            "Neuroticism both sit near the middle. His overall visibility is almost the highest in the chamber "
            "and remains above most after the Speakership is accounted for: role explains part, but not all, "
            "of how present he is in public settings."
        ),
        "p2": (
            "Across 2,482 covered turns on 22 programs, Johnson's average turn is somewhat shorter than most, "
            "but he concentrates the floor above most, enters rapid contested exchange above most, and makes "
            "explicit floor-defense moves around the top ten percent. The pre-Speakership long-form record adds "
            "an important condition to that aggregate. In a reviewed 2022 interview, one broad two-part policy "
            "question produced a single uninterrupted 134-second answer. He began with a governing maxim, moved "
            "through several examples, and only then stated the operational limit. In a one-on-one, therefore, "
            "a narrow question can keep the exchange brisk; a broad or compound prompt can yield a multi-minute "
            "set-piece answer. His 60 accepted audio clips also show one of the fastest voiced-segment rates in "
            "the chamber, short gaps, high pitch movement, and restrained loudness variation. Expect fast, "
            "emphatic delivery without assuming that speed means the answer will be short or the premise is open."
        ),
        "p3": (
            "Open with the decision, deadline, principle, and boundary in that order: 'We need your office to do "
            "X by Y. It advances P, and it does not require L.' Ask one narrow question first. If time is limited, "
            "do not lead with a two-part invitation to discuss the whole issue; the reviewed pre-role example "
            "shows that this can hand over the floor for several minutes. Bring one page with the requested "
            "action, implementation path, affected people, and one answer to the strongest objection. Let his "
            "first answer finish. If it rises to a general principle, acknowledge that principle once and narrow "
            "the next question to a choice: 'Given that, can the office support A, or is B the obstacle?' Because "
            "floor-defense language is repeated across 22 programs, rebut a challenged premise once with the "
            "strongest fact rather than stacking explanations. Outside pressure returns about 25 percent less "
            "than for the typical covered member, so close on a named office owner, one deliverable, and a date."
        ),
    },
    "Steve Scalise": {
        "p1": (
            "Steve Scalise's expert-perceived profile describes a settled, public-facing advocate. "
            "His Openness falls in the bottom four percent, meaning less inclination than almost any colleague "
            "to explore unfamiliar approaches, while Agreeableness sits around the bottom quarter and points to a "
            "harder interpersonal edge. Neither measure says he is withdrawn. His Extraversion sits in "
            "roughly the top quarter, and his public visibility is in the top two percent overall and the "
            "top quarter after adjusting for role: he is highly present even when his position is fixed. The "
            "same distinction resolves the work-ethic tension. The broad Conscientiousness estimate is "
            "around the bottom quarter, but his actual roll-call participation and bill follow-through "
            "exceed about seven in ten colleagues. One is a public personality estimate; the other is tracked execution. His "
            "wording also uses affective language less than most, a fact about vocabulary rather than "
            "evidence of what he feels."
        ),
        "p2": (
            "Across 1,048 covered turns on 11 programs, Scalise is ordinary in answer length but above "
            "most in explicit floor-defense moves, while rapid, interruption-heavy exchange is well "
            "below most. In a one-on-one, that is likely to sound like a stated position followed by a "
            "moderate-length explanation: expect him to defend the line, but do not expect constant "
            "cross-talk. Ask one question at a time, let the answer finish, and do not mistake the lack of "
            "interruption for willingness to reopen the premise. His accepted audio baseline spans "
            "23 clips across five episodes; it has shorter gaps, a brisk pace, restrained loudness "
            "variation, and a relatively narrow pitch range. The long-form layer now retains three complete "
            "reviewed answers across the supplied Punchbowl interview and the independent POLITICO source. "
            "The six short identity and acoustic samples from those sources show the same brisk, short-gap "
            "pattern but somewhat more pitch movement, so the useful description is controlled and compact, not flat. "
            "The leadership-era layer now adds a 2020 pre-Majority-Leader source. In its reviewed answer, a personal "
            "health check-in produced a detailed 71-second response before the program moved to policy. The other "
            "three reviewed answers were recorded after he became Majority Leader and remain current-role-conditioned. "
            "Context matters: "
            "his interview wording is midrange on antagonism, while his observed hearing behavior is in "
            "the top one percent. That is a hearing-room pattern, not a claim about private temperament."
        ),
        "p3": (
            "Start with a brief person-first or shared-context exchange, then move to the decision and deadline: "
            "'We need your office to do X by Y because Z.' The pre-role 2020 interview shows that a personal opening "
            "can produce a real answer rather than filler; two post-promotion POLITICO answers separately describe "
            "consequential work through individual conversations, prior familiarity, and trust. Together they support "
            "the sequence, but they do not prove that he always requires small talk or a conversation before every "
            "meeting. Budget for a bounded opening and make the pivot explicit. Page one should show the single "
            "requested action, the coalition behind it, the implementation "
            "path, and one concrete human consequence. That format fits his strong tracked execution better "
            "than an invitation to develop the idea in the room. Give the first answer room to run, then use "
            "one short factual follow-up rather than repeating the pitch. Outside pressure returns about 25 "
            "percent less than for the typical covered member, so precision is more useful than escalating "
            "force. Close by confirming the office owner, the next deliverable, and its date. If the exchange "
            "is a formal hearing, plan for a sharper edge than the interview record alone would suggest."
        ),
    },
    "Maxine Waters": {
        "p1": (
            "Maxine Waters's measures point to range, edge, and execution, but they answer different "
            "questions. Her expert-perceived Openness is in the top seven percent and her tracked policy "
            "breadth in the top ten, so both the broad read and the legislative record show unusually wide "
            "scope. Agreeableness falls in the bottom four percent, and her interview-language low-antagonism "
            "measure in the bottom one; those findings describe a sharper public style, not a lack of care for "
            "people. In fact, warmth words are in the top fifth and assertive words in roughly the top sixth. "
            "Her expert-perceived Neuroticism is in the top seven percent and affective wording in the top ten, "
            "but neither is a diagnosis or a reading of her inner state. The apparent discipline conflict "
            "is also specific: broad Conscientiousness falls in the lower third, while observed roll-call "
            "participation and bill follow-through exceed roughly two-thirds of colleagues. The work record is the direct measure "
            "of whether the work gets done."
        ),
        "p2": (
            "Waters controls airtime through repeated compact turns, not constant monologues. Her "
            "floor-holding concentration is in the top three percent, while average turn length is in the "
            "lower third; in the room, expect short, emphatic units that add up to control of the exchange. "
            "Rapid contested back-and-forth is among her lowest interaction features, so let her establish the "
            "sequence instead of trying to match each compact turn with another question. Her broader accepted "
            "audio record has longer gaps, a slower pace, greater loudness variation, and a narrower pitch "
            "range than most. The long-form layer now retains two complete reviewed answers, one from the requested "
            "Tavis Smiley interview and one from the separately found interview. The six short identity and acoustic "
            "samples establish the delivery comparison; in the complete requested closing answer, the gaps are shorter, the pace quicker, "
            "and the pitch movement wider than that aggregate. "
            "Accordingly, 'deliberate and level' is a baseline, not a fixed manner. Her observed hearing "
            "antagonism is above most, so formal proceedings can run sharper than an interview. Hear a compact "
            "reply as control of the turn, not as a signal that the subject has been exhausted."
        ),
        "p3": (
            "Open with one affected person or community, then state the action: 'This is who is being left out; "
            "we need your office to do X by Y.' Put the human consequence first on page one, followed by the "
            "number affected, the requested action, and the deadline. That ordering fits her demonstrated "
            "relationship-first framing better than beginning with institutional process. Let the first answer "
            "set the sequence; keep follow-ups short and do not pile a new question onto a compact reply. In a "
            "formal hearing, hold the factual line without matching the sharper register. Her response to outside "
            "pressure is only about 5 percent above the typical covered member, effectively near the middle, so "
            "human stakes and a workable action matter more than escalating force. Close by naming what the "
            "office will check, who owns it, and when you will hear back."
        ),
    },
    "Alex Padilla": {
        "p1": (
            "Alex Padilla's profile is cooperative and disciplined, with one setting-specific edge that "
            "should not be allowed to blur the rest. Expert-perceived Agreeableness exceeds about seven in ten colleagues "
            "and Conscientiousness sits in the top fifth. Separately, his tracked cross-party tie and cosponsorship shares "
            "are in the top five percent, and roll-call participation plus bill follow-through are in the top thirteen. "
            "Those measures are not interchangeable, but here they point in the same direction: a warmer public "
            "read, unusually cross-party legislative behavior, and strong execution. His interview language is "
            "midrange on antagonism, while observed hearing antagonism is above most. That does not "
            "erase the cooperation record; it says he uses a sharper style in hearings than his broader profile "
            "would predict. His public wording is exceptionally warm, in the top seven percent, while assertive "
            "wording is in the bottom twelve, another specific distinction between relational language and forceful phrasing."
        ),
        "p2": (
            "Padilla takes the floor less often than most, but speaks at length once he has it. Floor-holding "
            "concentration is in the bottom quarter and his broader conversational-dominance measure in the "
            "bottom fifth, while both average answer length and the long-answer tail are well above most. In the "
            "room, expect fewer entries into the exchange but a paragraph-length answer once he begins; do not "
            "stack questions or read a quiet opening as a short answer. These are different counts, not a contradiction. "
            "Across 52 accepted audio clips in ten episodes, he also combines longer between-phrase gaps with a "
            "high within-speech segment rate; pause length and speaking rate measure separate parts of the cadence. "
            "The long-form layer now retains two complete reviewed answers, one from the supplied New York Times "
            "interview and one from the separately found interview. Their six short identity and acoustic samples "
            "show gaps that remain long but a pace more measured than his NPR aggregate, "
            "evidence of setting variation rather than a new rank. Let the spaces between phrases sit. He makes "
            "explicit floor-defense moves above most across seven programs, so a challenged premise is likely to "
            "receive a full answer rather than a quick concession."
        ),
        "p3": (
            "Use the first 30 seconds for the outcome, the affected constituency, and the evidence that the proposal "
            "can work: 'This produces X result for Y people, and we are asking you to take Z action.' Page one should "
            "carry the result, two or three supporting facts, the implementation path, and a plausible partner "
            "across the aisle; his cross-party shares are among the highest in the covered chamber. State the human "
            "consequence rather than burying it, then ask one question and give the answer room to run. Do not fill "
            "the spaces between phrases. If he contests a premise, answer it directly and let him complete the reply "
            "before narrowing to the decision. Outside pressure performs about the same as it does for the typical "
            "member, so close on a specific result, a named office owner, and a date rather than on leverage alone."
        ),
    },
}


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


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8-sig"))
    evidence_doc = json.loads(EVIDENCE.read_text(encoding="utf-8-sig"))
    interview_doc = json.loads(INTERVIEWS.read_text(encoding="utf-8-sig"))
    interview_receipt = json.loads(INTERVIEW_RECEIPT.read_text(encoding="utf-8-sig"))
    long_turn_receipt = json.loads(LONG_TURN_RECEIPT.read_text(encoding="utf-8-sig"))
    long_turns = load_jsonl(LONG_TURNS)
    leadership_long_turn_receipt = json.loads(LEADERSHIP_LONG_TURN_RECEIPT.read_text(encoding="utf-8-sig"))
    leadership_long_turns = load_jsonl(LEADERSHIP_LONG_TURNS)
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8-sig"))
    if set(PROFILES) - set(artifact.get("members") or {}):
        raise SystemExit("target profile is missing from active artifact")
    verified_interview_members = set(interview_doc.get("members") or {}) | {
        row.get("member") for row in leadership_long_turns
    }
    if set(PROFILES) - verified_interview_members:
        raise SystemExit("target member is missing verified interview evidence")
    if interview_receipt.get("artifact_sha256") != sha256(INTERVIEWS):
        raise SystemExit("interview receipt is stale")
    if (long_turn_receipt.get("schema") != "member-interview-long-turn-receipt-v1" or
            long_turn_receipt.get("status") != "PASS" or
            long_turn_receipt.get("artifact_sha256") != sha256(LONG_TURNS)):
        raise SystemExit("long-form full-turn receipt is stale or invalid")
    if (leadership_long_turn_receipt.get("schema") != "member-leadership-prerole-long-turn-receipt-v1" or
            leadership_long_turn_receipt.get("status") != "PASS" or
            leadership_long_turn_receipt.get("artifact_sha256") != sha256(LEADERSHIP_LONG_TURNS)):
        raise SystemExit("leadership pre-role full-turn receipt is stale or invalid")
    turn_members = {row.get("member") for row in long_turns + leadership_long_turns}
    if set(PROFILES) - turn_members:
        raise SystemExit("target member is missing complete reviewed long-form turns")
    scalise_findings = {
        finding.get("kind")
        for row in long_turns if row.get("member") == "Steve Scalise"
        for finding in (row.get("semantic_findings") or [])
    }
    required_scalise_findings = {
        "individual_conversation_before_decision",
        "familiarity_and_trust_in_working_relationships",
    }
    if not required_scalise_findings <= scalise_findings:
        raise SystemExit("Scalise relationship-sequence findings are missing")

    evidence = evidence_doc["members"]
    for name, profile in PROFILES.items():
        draft = "\n".join((
            "**Overall personality.** " + profile["p1"],
            "**Manner of speech & landmines.** " + profile["p2"],
            "**Persuasive approach.** " + profile["p3"],
        ))
        issues = validator.mechanical_issues(draft, name, evidence[name])
        if issues:
            raise SystemExit(f"{name} failed deterministic narrative gates: {issues}")
        artifact["members"][name].update(profile)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    artifact["basis_sha256"] = evidence_doc["basis_sha256"]
    artifact.setdefault("provenance", {})["targeted_audio_refresh_20260903"] = {
        "completed_utc": now,
        "members": sorted(PROFILES),
        "reason": "User-supplied long-form interviews, Google-found companion-source catalog, and request to replace vague or apparently contradictory prose.",
        "interview_artifact": str(INTERVIEWS.relative_to(ROOT)).replace("\\", "/"),
        "interview_artifact_sha256": sha256(INTERVIEWS),
        "verified_sources": interview_receipt["counts"]["verified_sources"],
        "accepted_exact_windows": interview_receipt["counts"]["accepted_exact_windows"],
        "complete_reviewed_turns": long_turn_receipt["counts"]["turns"],
        "complete_reviewed_turn_minutes": long_turn_receipt["counts"]["total_turn_minutes"],
        "longest_reviewed_turn_seconds": long_turn_receipt["counts"]["longest_turn_seconds"],
        "maximum_turn_seconds": long_turn_receipt["contract"]["maximum_turn_seconds"],
        "long_turn_artifact": str(LONG_TURNS.relative_to(ROOT)).replace("\\", "/"),
        "long_turn_artifact_sha256": sha256(LONG_TURNS),
        "leadership_prerole_long_turn_artifact": str(LEADERSHIP_LONG_TURNS.relative_to(ROOT)).replace("\\", "/"),
        "leadership_prerole_long_turn_artifact_sha256": sha256(LEADERSHIP_LONG_TURNS),
        "leadership_prerole_complete_reviewed_turns": leadership_long_turn_receipt["counts"]["turns"],
        "construct_rule": "Trait estimates, observed behavior, public wording, setting-specific behavior, and delivery measurements are stated separately; disagreement is explained rather than averaged away.",
        "leadership_audio_rule": "Pre-current-role audio supplies the baseline for formal leaders; post-role interviews are retained as current-role-conditioned evidence and cannot alone establish a stable trait.",
        "engine_effect": "NONE; descriptive profile text and evidence provenance only",
    }
    write_json_atomic(ARTIFACT, artifact)
    active_hash = sha256(ARTIFACT)

    receipt["created_utc"] = now
    receipt["basis_sha256"] = evidence_doc["basis_sha256"]
    receipt["evidence_sha256"] = sha256(EVIDENCE)
    receipt["candidate_sha256"] = active_hash
    receipt["active_artifact_sha256"] = active_hash
    receipt.setdefault("counts", {})["targeted_audio_refresh"] = len(PROFILES)
    receipt["targeted_audio_refresh"] = artifact["provenance"]["targeted_audio_refresh_20260903"]
    for name in PROFILES:
        receipt["members"][name] = {
            "accepted_source": "targeted_audio_refresh_20260903",
            "semantic_pass": True,
            "reviewer": "deterministic evidence gates plus manual construct-separation review",
            "rounds": "one targeted rewrite",
        }
    write_json_atomic(RECEIPT, receipt)
    print(json.dumps({"members": sorted(PROFILES), "active_artifact_sha256": active_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
