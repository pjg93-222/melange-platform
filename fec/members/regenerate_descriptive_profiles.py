"""Build reproducible public-record profiles, without API calls.

Paul ruled on 2026-09-07 that word count does not matter.  The PUBLICATION bound is
therefore only a defect guard -- MIN_WORDS catches a collapsed paragraph, MAX_WORDS
catches runaway output -- and no MANDATORY sentence is ever skipped to fit it.  compose()
keeps a separate packing budget (TARGET_WORDS/OVER_BUDGET) that decides how
much OPTIONAL detail a profile carries; that budget is a style choice, not a gate.

Never consumes personality, dominance, persuasion, or meeting-playbook fields.
Generation validation is NOT a new audio review or semantic model certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fec.members.descriptive_profile_overrides import OVERRIDES
from fec.members.descriptive_public_accounts import PUBLIC_ACCOUNTS
from fec.members.validate_descriptive_profiles import (
    CONTRACT, MAX_WORDS, MIN_WORDS, SCHEMA, STATUS,
    validate_descriptive_profiles, word_count,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/members/member_narrative_evidence_v4.json"
BASIS = ROOT / "data/members/profile_basis_v2.json"
ACTIVE = ROOT / "member_narratives_v2.json"
RECEIPT = ROOT / "data/members/member_narratives_v3_descriptive_receipt.json"
OUT = ROOT / "audits/descriptive_profiles_20260904"
SETTINGS = {
    "house_floor": "House-floor speeches", "npr": "NPR interviews",
    "sunday_show": "Sunday news interviews", "washington_journal": "Washington Journal interviews",
    "podcast_interview": "podcast interviews", "broadcast_news_interview": "broadcast news interviews",
    "longform_podcast": "long-form podcasts",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def position(pct):
    if pct < .20:
        return "well below the middle"
    if pct < .40:
        return "below the middle"
    if pct <= .60:
        return "near the middle"
    if pct <= .80:
        return "above the middle"
    return "well above the middle"


def joined(items):
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def public_quote(name, speech):
    """Only complete verbatim self-account sentences; never use generated claims."""
    approved = PUBLIC_ACCOUNTS.get(name)
    if not approved:
        return None
    candidates = []
    for item in speech.get("publicly_expressed_sensibilities") or []:
        excerpt = re.sub(r"\s+", " ", item.get("public_evidence_excerpt") or "").strip()
        for sentence in re.split(r"(?<=[.!?])\s+", excerpt):
            if sentence != approved:
                continue
            if not re.match(r"(?:I |My |Our family )", sentence) or not sentence.endswith((".", "!", "?")):
                continue
            if not 10 <= len(sentence.split()) <= 25:
                continue
            if not re.search(r"\b(grew|born|remember|served|worked|learned|attended|raised|family|parents|grandfather|grandmother|father|mother)\b", sentence, re.I):
                continue
            if any(x in sentence.lower() for x in ("yield", "colleague", "rise today", "mr.", "madam", "congratulate")):
                continue
            candidates.append((sentence, item))
    return candidates[0] if candidates else None


def compose(name, record):
    speech = record.get("speech_profile") or {}
    behavior = record.get("behavior") or {}
    raw = ((speech.get("general_word_habits") or {}).get("raw_text_observations") or {})
    # Two complementary audio layers.  Until 2026-09-07 only the first was read here, so
    # the 74 members whose only audio is an identity-verified voiceprint were told in prose
    # that no accepted audio sample existed -- false for members carrying tens of minutes of
    # it.  Each layer now gets its own sentence on its own scale, and the no-audio
    # disclosure is reserved for members who genuinely carry neither.
    audio = speech.get("general_audio_habits") or {}
    voiceprint = speech.get("verified_voiceprint_baseline") or {}
    paragraphs = [[], [], []]
    notes = []
    breadth = behavior.get("policy_breadth") or {}
    if breadth.get("pct") is not None:
        paragraphs[0].append(f"{name}'s issue-coverage measure ranks {position(breadth['pct'])} of the covered sample. It compares the spread of recorded legislative involvement across issue areas in the tracked bills archive.")
    else:
        paragraphs[0].append(f"{name}'s current evidence summary does not provide a usable comparison of the number of legislative issue areas covered.")
    diligence = behavior.get("legislative_diligence") or {}
    if diligence.get("pct") is not None:
        paragraphs[0].append(f"The combined measure of recorded voting participation and bill follow-through is {position(diligence['pct'])} among covered members, using whichever components are available.")
    else:
        paragraphs[0].append("The combined voting-participation and bill-follow-through comparison is also unavailable; that gap is not a negative performance rating.")
    cross = behavior.get("cross_party_cooperation") or {}
    if cross.get("pct") is not None:
        paragraphs[0].append(f"The share-based measure of cross-party legislative connections is {position(cross['pct'])} of the sample; it measures recorded ties and cosponsorship, not personal friendliness.")
    else:
        paragraphs[0].append("There is no usable cross-party comparison in this summary, so it does not establish a tendency to work across party lines or avoid doing so.")

    words, appearances = int(raw.get("interview_words") or 0), int(raw.get("appearances") or 0)
    if words:
        paragraphs[1].append(f"The interview archive contains {words:,} attributed words across {appearances:,} archived appearances.")
        median = raw.get("median_words_per_turn")
        if median is not None:
            paragraphs[1].append(f"A typical stored passage is about {round(median):,} words, but these excerpts are not verified complete answers and cannot establish how long an uninterrupted response lasts.")
    else:
        paragraphs[1].append("The current interview archive has no usable attributed-word sample for this member. That means answer length, question handling, and conversational give-and-take remain unassessed here.")
    settings = [SETTINGS.get(k, k.replace("_", " ")) for k, v in (speech.get("by_setting") or {}).items() if v.get("clips")]
    voiceprint_minutes = (f"{float(voiceprint['minutes']):.1f}"
                          if voiceprint.get("minutes") is not None else None)
    if audio.get("clips"):
        clips, episodes = int(audio["clips"]), int(audio.get("episodes") or 0)
        minutes = f"{float(audio.get('minutes') or 0):.1f}"
        clip_word, episode_word = ("clip" if clips == 1 else "clips"), ("episode" if episodes == 1 else "episodes")
        paragraphs[1].append(f"Separately, the audio collection includes {clips} {clip_word} from {episodes} {episode_word}, totaling about {minutes} minutes" + (f", covering {joined(settings)}." if settings else "."))
        # Floor audio is labelled, never removed: the pooled figures above are a
        # clip-weighted mean, so a floor-heavy pool describes floor delivery.
        composition = (audio.get("floor_composition") or {}).get("composition")
        if composition in ("floor_only", "floor_dominant"):
            paragraphs[1].append(
                ("%s of that audio is House-floor recording, where the microphone and speaking "
                 "format differ from an interview, so the figures describe floor delivery.")
                % ("All" if composition == "floor_only" else "Most"))
    if voiceprint_minutes is not None and audio.get("clips"):
        paragraphs[1].append(
            f"About {voiceprint_minutes} minutes of identity-verified audio is also held as one "
            "continuous voiceprint, standardized within its own collection and not on the clip scale.")
    elif voiceprint_minutes is not None:
        paragraphs[1].append(
            f"The audio held for this member is an identity-verified recording of about "
            f"{voiceprint_minutes} minutes, kept as one continuous voiceprint rather than cut into "
            "clips, so its measurements are standardized within that collection and support no "
            "clip-by-clip or setting-by-setting comparison.")
    elif not audio.get("clips"):
        paragraphs[1].append("There is no accepted audio sample in this evidence summary, so it cannot support a description of vocal delivery.")

    quote = public_quote(name, speech)
    if quote:
        sentence, item = quote
        source = "Congressional Record remarks" if item.get("source") == "CREC" else "archived public remarks"
        paragraphs[2].append(f"In {source} dated {item.get('date') or 'date unavailable'}, {name} said: “{sentence}”")
        paragraphs[2].append("This is a specific public account, not evidence of a recurring conversational habit or a private preference.")
        notes.append({"type": "verbatim_public_account", "source": item.get("source"), "date": item.get("date"), "locator": item.get("locator"), "excerpt": sentence})
    else:
        paragraphs[2].append("The clearest distinction is between a public record of activity and a demonstrated conversational habit: this summary supports the former more strongly than the latter.")
    paragraphs[2].append("A recurring communication pattern still requires the same observed behavior in two independently produced interviews in different settings. Public recordings alone do not establish what happens in a private meeting.")

    optional = [
        (0, "Issue coverage describes recorded activity, not curiosity, expertise, or willingness to consider another view."),
        (1, "The archive's coverage does not establish how many independently produced long-form interviews have been fully reviewed."),
        (0, "The combined legislative measure does not establish each component separately or predict office responsiveness."),
        (2, "Leadership responsibilities, the interviewer's questions, and the recording format can shape what appears in public."),
        (2, "Missing evidence should stay an open question, not become a description of the person."),
        (1, "A complete answer may last many minutes; passage boundaries are not a suitable substitute for listening through the exchange."),
    ]
    def count():
        return len(" ".join(" ".join(p) for p in paragraphs).split())
    # COMPOSER BUDGET, not the publication bound.  These two numbers decide how much
    # OPTIONAL detail a profile carries; MIN_WORDS/MAX_WORDS decide whether it may
    # publish at all.
    #
    # OVER_BUDGET was raised from 250 to 320 on 2026-09-07.  The quote-drop branch below
    # discards a VERBATIM public quote together with its source note -- silent loss of
    # the one piece of externally checkable provenance a profile carries.
    #
    # Measured, not estimated.  On the CURRENT evidence artifact the widest MANDATORY-only
    # profile is 211 words (Tim Kaine; Chip Roy 210), so a 250 ceiling left 39 words of
    # margin.  The evidence has not yet been regenerated: verified_voiceprint_baseline is
    # a new key that build_member_narrative_ledger.py emits and that the on-disk
    # member_narrative_evidence_v4.json does not carry, so the second audio sentence does
    # not fire yet.  Simulating the regenerated artifact by giving every member a
    # voiceprint, the widest mandatory-only profile becomes 235 words (Kaine; Roy 234) --
    # a 15-word margin under 250, thinner than one sentence.  320 restores an 85-word
    # margin and still sits 80 words under MAX_WORDS, so the composer cannot build a row
    # the publication gate then rejects: 320 plus the longest optional sentence (20 words)
    # stays well under 400.
    #
    # Effect of the raise, measured on both artifacts: ZERO members change.  The branch is
    # unreachable in both states, so this is prophylactic, not corrective.  The second use
    # of OVER_BUDGET (the optional-sentence admission test) was already non-binding: the
    # loop breaks at TARGET_WORDS=205 and no optional sentence exceeds 20 words.
    TARGET_WORDS, OVER_BUDGET = 205, 320
    # Omit optional detail as whole sentences; never truncate a quote or a caveat.
    if count() > OVER_BUDGET and quote:
        paragraphs[2] = [
            "Single-source observations remain provisional. A recurring communication pattern requires the same observed behavior in two independently produced interviews in different settings.",
            "Public recordings alone do not establish what happens in a private meeting.",
        ]
        notes = []
    for index, sentence in optional:
        if count() >= TARGET_WORDS:
            break
        if count() + len(sentence.split()) <= OVER_BUDGET:
            paragraphs[index].append(sentence)
    result = {"p%d" % (i + 1): " ".join(p) for i, p in enumerate(paragraphs)}
    result["sources"] = notes
    return result


def build_rows(evidence):
    result = {}
    for name, record in sorted(evidence["members"].items()):
        row = dict(OVERRIDES[name]) if name in OVERRIDES else compose(name, record)
        row.update(ok=True, format="descriptive-v3", recurring_audio_pattern_claimed=False)
        row["words"] = word_count(row)
        row["evidence_locator"] = "data/members/member_narrative_evidence_v4.json#/members/" + name
        row["generation"] = "evidence-checked-editorial-override" if name in OVERRIDES else "grounded-descriptive-template"
        if not MIN_WORDS <= row["words"] <= MAX_WORDS:
            raise ValueError(f"{name}: {row['words']} words "
                             f"(outside {MIN_WORDS}-{MAX_WORDS})")
        result[name] = row
    return result


def write_bytes_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path, value):
    write_bytes_atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true", help="Back up and replace the active profile corpus after validation")
    parser.add_argument("--output-dir", type=Path, default=OUT,
                        help="Generation artifacts and previous-active backup directory")
    args = parser.parse_args()
    OUT = args.output_dir
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    basis = json.loads(BASIS.read_text(encoding="utf-8"))
    if (evidence.get("schema") != "member-narrative-evidence-v4"
            or evidence.get("status") != "ACTIVE_FAIL_CLOSED_INPUT"
            or evidence.get("basis_sha256") != digest(BASIS)
            or set(evidence["members"]) != set(basis["members"])):
        raise SystemExit("Evidence or roster contract mismatch")
    members = build_rows(evidence)
    if len(members) != 533 or members != build_rows(evidence):
        raise SystemExit("Corpus coverage or reproducibility failure")
    doc = {
        "schema": SCHEMA, "status": STATUS, "contract_version": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "basis_sha256": digest(BASIS), "evidence_sha256": digest(EVIDENCE),
        "count": len(members), "ok_count": len(members),
        "provenance": {"method": "deterministic evidence-scoped prose plus four editorial profiles",
                       "api_calls": 0, "new_audio_reviews": 0,
                       "generator_sha256": digest(Path(__file__)),
                       "editorial_overrides_sha256": digest(Path(__file__).with_name("descriptive_profile_overrides.py")),
                       "public_accounts_allowlist_sha256": digest(Path(__file__).with_name("descriptive_public_accounts.py")),
                       "validation_scope": "word limits, exact roster, reproducible approved-field templates, receipt hashes; not new human audio validation"},
        "members": members,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    candidate = OUT / "member_profiles.json"
    write_json(candidate, doc)
    receipt = {
        "schema": "member-narratives-v3-descriptive-receipt-v1", "status": "PASS",
        "contract_version": CONTRACT, "basis_sha256": digest(BASIS),
        "evidence_sha256": digest(EVIDENCE), "active_artifact_sha256": digest(candidate),
        "counts": {"profiles": 533, "word_count_pass": 533, "grounded_template_pass": 533,
                   "editorial_overrides": len(OVERRIDES), "new_audio_reviews": 0},
        "validation_scope": doc["provenance"]["validation_scope"],
        "semantic_model_review": False, "independent_human_audio_review": False,
        "minimum_words": min(r["words"] for r in members.values()),
        "maximum_words": max(r["words"] for r in members.values()),
    }
    errors = validate_descriptive_profiles(doc, receipt, basis_sha256=digest(BASIS),
        evidence_sha256=digest(EVIDENCE), active_artifact_sha256=digest(candidate), member_names=basis["members"])
    if errors:
        raise SystemExit("\n".join(errors))
    write_json(OUT / "validation_receipt.json", receipt)
    markdown = ["# Member profiles — plain-language edition", "", "533 profiles, %d–%d words each (observed range, not a target). Public-record descriptions; no private-meeting predictions. Existing evidence only; no new audio was reviewed in this regeneration." % (receipt["minimum_words"], receipt["maximum_words"]), ""]
    for name, row in members.items():
        markdown.extend(["## " + name, "", "### Public record", "", row["p1"], "", "### Public communication", "", row["p2"], "", "### Context and evidence", "", row["p3"], "", f"*{row['words']} words · {row['generation']}*", ""])
        markdown.extend([f"Evidence: [member evidence ledger](<{EVIDENCE.as_posix()}>), entry **{name}**.", ""])
        for source in row.get("sources") or []:
            if source.get("url"):
                markdown.extend([f"Source: [{source.get('title') or source['url']}]({source['url']}) — {source.get('date') or source.get('published') or 'date in source metadata'}.", ""])
            elif source.get("locator"):
                markdown.extend([f"Source: {source.get('source')}, {source.get('date')} — {source['locator']}.", ""])
    (OUT / "MEMBER_PROFILES.md").write_text("\n".join(markdown), encoding="utf-8")
    if args.activate:
        backup = OUT / "previous_active"
        backup.mkdir(exist_ok=True)
        for original in (ACTIVE, ROOT / "data/members/member_narratives_v2_semantic_receipt.json", RECEIPT):
            destination = backup / original.name
            if not original.exists():
                continue
            if not destination.exists():
                destination.write_bytes(original.read_bytes())
            elif destination.read_bytes() != original.read_bytes():
                # re-activation: keep a timestamped copy instead of losing the current live file
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                (backup / f"{original.stem}.{stamp}{original.suffix}").write_bytes(original.read_bytes())
        write_bytes_atomic(ACTIVE, candidate.read_bytes())
        write_json(RECEIPT, receipt)
    print(json.dumps({"profiles": len(members), "words_min": receipt["minimum_words"], "words_max": receipt["maximum_words"], "activated": args.activate, "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
