"""v3 member briefs: packet builder, validator, publication gate and the regeneration guard.

Fixture members are synthetic ledger records.  Tests that need the real (gitignored) ledger or the
gold exemplars skip cleanly when those files are absent.
"""
import copy
import csv
import json
import os
import re
from pathlib import Path

import pytest

from fec.members import build_member_brief_packets as P
from fec.members import regenerate_member_profiles as RMP
from fec.members import validate_member_briefs_v3 as V

ROOT = Path(__file__).resolve().parents[3]
PCT = {"among the highest in the chamber": .99, "well above most": .86, "above most": .66, "middling": .5,
       "below most": .34, "well below most": .14, "among the lowest in the chamber": .01}


def B(label):
    return {"pct": PCT[label], "label": label, "neutral": label == "middling"}


def record(chamber="House", *, traits=None, reads=None, behavior=None, facets=None, measures=None, audio=None,
           defense=None, hearing=None, visible=None, pressure="pressure returns about the same as for the typical covered member",
           frame=None, supported=(), sens=(), turns=0, contrasts=(), leadership=None, longform=None):
    traits = traits or {}
    rec = {
        "name": "x", "chamber": chamber,
        "traits": {k: {"band": B(traits.get(k, "middling"))} for k in
                   ("Extraversion", "Openness", "Conscientiousness", "Agreeableness", "Neuroticism")},
        "qualified_reads": {k: {"band": B(v)} for k, v in (reads or {}).items()},
        "behavior": {k: B(v) for k, v in (behavior or {}).items()},
        "visibility": {"observed": B(visible) if visible else None, "role_adjusted": B(visible) if visible else None},
        "hearing_antagonism": {"band": B(hearing), "present": True} if hearing else None,
        "receptivity": {"pressure_note": pressure, "susc_read": "mixed"},
        "effectiveness": {"do_pct": .5, "les_pct": .5, "ft": 1.0, "kb_pct": .5},
        "speech_profile": {
            "general_word_habits": {"communication_facets": {k: {"band": B(v)} for k, v in (facets or {}).items()},
                                    "raw_text_observations": {"interview_turns": turns}},
            "interaction_habits": ({"turns": turns, "measures": {k: B(v) for k, v in measures.items()}} if measures else None),
            "general_audio_habits": audio,
            "repeated_defensive_reactions": ({"band": B(defense), "turns": turns, "independent_programs": 4} if defense else None),
            "publicly_expressed_sensibilities": [dict(s) for s in sens],
            "motivating_appeals": {"agency_percentile": .9, "communion_percentile": .2, "assertive_vs_communicative_frame": frame,
                                   "supported_dimensions": list(supported)},
            "supported_topic_associations": [], "setting_contrasts": list(contrasts),
            "longform_interview_evidence": longform,
        },
    }
    if leadership:
        rec["leadership_audio_source_policy"] = leadership
    return rec


def audio(clips=12, floor="no_floor", pace="well above most"):
    return {"clips": clips, "channels": {"pace": {"band": B(pace)}, "pause": {"band": B("middling")},
                                          "volume_dyn": {"band": None}, "pitch_var": {"band": B("above most")}},
            "floor_composition": {"composition": floor}}


OUTCOME = ("Lead with outcomes: a short, direct, results-focused brief", ["assertive, outcome-led, or immediate framing"])
COMMUNITY = ("Relationship-first: constituent stories and community framing", ["communicative, community, or human-impact framing"])
MIXED = ("Mixed interpersonal style: read the room", [])
LEADER = {"current_role": "Speaker of the House", "current_role_assumed_on": "2023-10-25", "verified_post_role_source_count": 0}


def ledger():
    rich = record(
        traits={"Agreeableness": "well below most", "Conscientiousness": "above most", "Neuroticism": "above most"},
        reads={"conscientiousness_diligence": "well above most", "neuroticism_affective_content": "well below most"},
        behavior={"legislative_diligence": "among the highest in the chamber", "conversational_dominance": "above most",
                  "words_per_turn": "below most"},
        facets={"assertiveness": "below most"}, turns=1800,
        measures={"wpt_mean": "below most", "discmark": "among the lowest in the chamber", "defense": "well above most"},
        audio=audio(), defense="well above most", hearing="among the lowest in the chamber", visible="among the highest in the chamber",
        pressure="pressure returns about 20% less than for the typical covered member; a discount, not immunity",
        frame=OUTCOME[0], supported=OUTCOME[1],
        contrasts=[{"first": "npr", "second": "sunday_show", "first_clips": 40, "second_clips": 30, "delta_first_minus_second": {"pace": -2.0}}])
    thin = record(
        traits={"Extraversion": "among the lowest in the chamber", "Conscientiousness": "well above most"},
        behavior={"legislative_diligence": "among the highest in the chamber", "cross_party_cooperation": "below most"},
        reads={"conscientiousness_diligence": "among the highest in the chamber"}, hearing="among the lowest in the chamber",
        frame=MIXED[0], supported=MIXED[1],
        sens=[{"category": "formative", "claim": "He grew up in a small town after a flood.", "date": "2025-01-01", "source": "CREC"}])
    floor = record(
        traits={"Extraversion": "above most"}, facets={"warmth": "well above most"}, turns=900,
        measures={"burst": "above most"}, audio=audio(clips=3, floor="floor_only"),
        frame=COMMUNITY[0], supported=COMMUNITY[1],
        sens=[{"category": "family", "claim": "A parent raised the family on a farm.", "date": "2025-02-01", "source": "CREC"},
              {"category": "family", "claim": "A parent raised the family on a farm for years.", "date": "2025-03-01", "source": "CREC"}])
    for name, rec, chamber in (("Alex Rich", rich, "House"), ("Beth Thin", thin, "Senate"), ("Cam Floor", floor, "House")):
        rec["name"], rec["chamber"] = name, chamber
    return {"schema": "member-narrative-evidence-v4", "status": "ACTIVE_FAIL_CLOSED_INPUT", "sources_sha256": {},
            "members": {"Alex Rich": rich, "Beth Thin": thin, "Cam Floor": floor}}


LEGS = [
    {"bioguide_id": "A1", "gender": "M", "party": "Republican", "last_name": "Rich"},
    {"bioguide_id": "B1", "gender": "F", "party": "Democrat", "last_name": "Thin"},
    {"bioguide_id": "C1", "gender": "", "party": "Independent", "last_name": "Floor"},
]
CONG = [{"member": "Alex Rich", "bioguide": "A1", "chamber": "House"}, {"member": "Beth Thin", "bioguide": "B1", "chamber": "Senate"},
        {"member": "Cam Floor", "bioguide": "C1", "chamber": "House"}]
DESK = {"members": [{"name": "Alex Rich", "district": "Congressional District 4 (119th Congress), Florida", "committee": {"on": "Senate Small Business"}},
                    {"name": "Beth Thin", "district": "Oklahoma"}, {"name": "Cam Floor", "district": ""}]}
COUNTS = {"Alex Rich": {"floor_speeches": 400, "floor_from": "2019-03-01", "floor_to": "2026-10-01", "hearings": 30,
                        "hearing_from": "2018-05-02", "hearing_to": "2026-09-01"},
          "Beth Thin": {"floor_speeches": 3, "floor_from": "2025-01-10", "floor_to": "2025-01-10", "hearings": 0,
                        "hearing_from": None, "hearing_to": None},
          "Cam Floor": {"floor_speeches": 60, "floor_from": "2024-02-01", "floor_to": "2026-08-01", "hearings": 1,
                        "hearing_from": "2025-01-01", "hearing_to": "2025-01-01"}}


def level(name, lvl="TRANSCRIPT_ATTRIBUTED", **kw):
    return {"level": lvl, "recordings": 1, "hours": .5, "date_from": None, "date_to": None, "reviewed_findings": [],
            "provisional_findings": [], "exception_reason": kw.get("reason")}


def levels(status="FINAL", **overrides):
    members = {n: level(n) for n in ("Alex Rich", "Beth Thin", "Cam Floor")}
    members.update(overrides)
    return {"status": status, "generated_at": "2026-10-20T00:00:00+00:00", "members": members, "census": {}}


def build(**kw):
    args = dict(legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS,
                levels_doc=levels())
    args.update(kw)
    return P.build_all(ledger(), **args)


# ---------------------------------------------------------------------------- packet builder ----
def test_three_fixture_members_build_with_pronoun_tier_and_archetype():
    packets, problems = build()
    assert problems == []
    rich, thin, floor = (packets[n] for n in ("Alex Rich", "Beth Thin", "Cam Floor"))
    assert [p["member"]["pronoun"] for p in (rich, thin, floor)] == ["he", "she", "they"]
    assert floor["member"]["pronoun_source"].startswith("gender absent")
    assert [p["evidence_tier"] for p in (rich, thin, floor)] == ["rich", "thin", "standard"]
    assert rich["suggested_animal"] == "Bull" and rich["coin_recommended"]["value"] is False
    assert thin["suggested_animal"] == "Tortoise"            # thin tier accepts three matched slots, and so does the rule
    assert rich["member"]["bio"]["state"] == "Florida" and rich["member"]["bio"]["district"] == "4th district"
    assert rich["member"]["bio"]["committees"] == ["Senate Small Business"]
    assert rich["member"]["surname"] == "Rich" and all(p["ready_for_writing"] for p in packets.values())


def test_plain_phrases_keep_band_strength_and_inflect_for_they():
    packets, _ = build()
    items = {i["id"]: i for i in packets["Alex Rich"]["evidence"]}
    assert items["TR.A"]["plain"] == "He is quite skeptical"                     # well below: "quite", never "one of the most"
    assert items["BH.diligence"]["plain"] == "He is one of the most reliable members at voting and seeing bills through in the House"
    assert items["TR.C"]["plain"] == "He is fairly careful and organized"            # above most: never "very"
    assert items["QR.diligence"]["plain"].startswith("He talks about his work in much more careful")  # well above: never "one of the most"
    assert items["BH.diligence"]["strength"] == "outer"
    senate = {i["id"]: i for i in packets["Beth Thin"]["evidence"]}
    assert senate["TR.E"]["plain"] == "She is one of the most reserved members in the Senate"   # chamber word follows the chamber
    they = {i["id"]: i for i in packets["Cam Floor"]["evidence"]}
    assert they["TR.E"]["plain"] == "They are fairly outgoing"
    assert they["IX.burst"]["plain"].startswith("They answer in quick runs")                   # plural verb, no -s


def test_neutral_items_are_flagged_and_never_citable():
    packets, _ = build()
    items = {i["id"]: i for i in packets["Alex Rich"]["evidence"]}
    assert items["TR.E"]["neutral"] is True and items["TR.E"]["plain"] is None and items["TR.E"]["may_support"] == []
    assert items["RC"]["neutral"] is False and items["RC"]["direction"] == "less"
    assert {i["id"]: i for i in packets["Beth Thin"]["evidence"]}["RC"]["neutral"] is True      # "about the same"


def test_permission_matrix_follows_the_guide():
    assert P.permission_for("TR.N") == {"archetype", "personality"}
    assert "follow_up" not in P.permission_for("TR.A") and "speaks" not in P.permission_for("TR.A")
    assert "warning_sign" not in P.permission_for("BH.coop") and "follow_up" not in P.permission_for("IX.burst")
    assert P.permission_for("LD") == P.permission_for("BIO.state") == {"personality", "decides"}
    assert P.PERMISSION["LF.provisional"] == {"speaks"} and "personality" not in P.PERMISSION["LF.qualified"]
    assert P.permission_for("PROC") == {"do", "dont", "warning_sign", "follow_up"}
    assert P.permission_for("BH.breadth") == {"archetype", "personality", "decides"}                       # description only (B8)
    for field in ("bottom_line", "do", "dont", "follow_up", "warning_sign", "speaks"):
        assert field not in P.permission_for("BH.breadth")
    assert {"bottom_line", "do", "dont", "follow_up"} <= P.permission_for("BH.diligence") and "bottom_line" in P.permission_for("BH.coop")


def test_defense_and_cross_party_items_exist_only_behind_their_gates():
    packets, _ = build()
    rich, thin = packets["Alex Rich"], packets["Beth Thin"]
    ids = {i["id"] for i in rich["evidence"]}
    assert rich["gates"]["DF"] is True and {"DF", "IX.defense"} <= ids and rich["gates"]["BH.coop"] is False
    assert "BH.coop" not in ids
    assert thin["gates"]["BH.coop"] is True and thin["gates"]["DF"] is False
    assert not ({"DF", "IX.defense"} & {i["id"] for i in thin["evidence"]})
    assert rich["gates"]["party_unity"] is False and rich["gates"]["topic_associations"] == []


@pytest.mark.parametrize("frame,supported,dim", [
    (OUTCOME[0], OUTCOME[1], "outcome"), (COMMUNITY[0], COMMUNITY[1], "community"),
    ("Partnership framing: offer a visible leadership role", OUTCOME[1] + COMMUNITY[1], "partnership"),
    ("Technical brief: data, evidence", ["technical, factual, and written-evidence framing"], "technical"),
    ("Direct style: be crisp", OUTCOME[1], "outcome"), ("Warmth-forward style: open", COMMUNITY[1], "community")])
def test_appeal_types_normalize_to_one_dimension(frame, supported, dim):
    got = P.normalize_appeals({"assertive_vs_communicative_frame": frame, "supported_dimensions": supported})
    assert got["dims"] == [dim]


def test_appeal_frame_that_disagrees_with_its_dimensions_fails_closed():
    with pytest.raises(ValueError):
        P.normalize_appeals({"assertive_vs_communicative_frame": OUTCOME[0], "supported_dimensions": COMMUNITY[1]})
    with pytest.raises(ValueError):
        P.normalize_appeals({"assertive_vs_communicative_frame": "Something new", "supported_dimensions": []})
    assert P.normalize_appeals({"assertive_vs_communicative_frame": None}) is None


def test_receptivity_strength_follows_the_stated_percentage_and_same_is_neutral():
    mild = P.receptivity_item({"pressure_note": "pressure returns about 9% less than for the typical covered member"}, "she")
    assert mild["plain"] == "outside pressure moves her a little less than it moves most members" and mild["strength"] == "mild"
    assert P.receptivity_item({"pressure_note": "pressure returns about 37% more than for the typical covered member"}, "he")["strength"] == "outer"
    assert P.receptivity_item({"pressure_note": "pressure returns about the same as for the typical covered member"}, "he")["neutral"]


def test_archetype_disqualifies_on_strongly_opposite_slot_and_coins_below_threshold():
    packets, _ = build()
    cands = {c["animal"]: c for c in packets["Alex Rich"]["archetype_candidates"]}
    assert cands["Bull"]["matched"] == 6 and not cands["Bull"]["disqualified"]
    loose = record(traits={"Conscientiousness": "well below most", "Agreeableness": "well below most"},
                   behavior={"legislative_diligence": "well below most", "conversational_dominance": "above most"},
                   facets={"assertiveness": "above most"}, defense="well above most", measures={"wpt_mean": "below most"},
                   turns=900, audio=audio(), frame=OUTCOME[0], supported=OUTCOME[1])
    led = ledger()
    led["members"]["Alex Rich"] = {**loose, "name": "Alex Rich", "chamber": "House"}
    packets, _ = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={},
                             basis_counts=COUNTS, levels_doc=levels())
    bull = {c["animal"]: c for c in packets["Alex Rich"]["archetype_candidates"]}["Bull"]
    assert bull["disqualified"] and "slot 3 strongly opposite" in bull["contradicted"]
    empty = P.build_all({**ledger(), "members": {"Cam Floor": {**record(), "name": "Cam Floor", "chamber": "House"}}},
                        legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS,
                        levels_doc=levels())[0]["Cam Floor"]
    assert empty["coin_recommended"]["value"] is True and "no template animal reaches" in empty["coin_recommended"]["reason"]


def test_setting_contrast_is_meaningful_only_with_clips_spread_and_no_floor():
    led = ledger()
    base = {"first": "npr", "second": "sunday_show", "first_clips": 40, "second_clips": 30}
    members = {}
    for i in range(40):                       # a population with a real spread for this pair
        rec = record(contrasts=[{**base, "delta_first_minus_second": {"pace": (i - 20) / 20}}])
        members["M%d" % i] = {**rec, "name": "M%d" % i, "chamber": "House"}
    members["Wide"] = {**record(contrasts=[{**base, "delta_first_minus_second": {"pace": 3.0}}]), "chamber": "House"}
    members["Few"] = {**record(contrasts=[{**base, "first_clips": 2, "delta_first_minus_second": {"pace": 3.0}}]), "chamber": "House"}
    members["Floor"] = {**record(contrasts=[{**base, "first": "house_floor", "delta_first_minus_second": {"pace": 3.0}}]), "chamber": "House"}
    stats = P.contrast_stats(members)
    wide = P.contrast_items(members["Wide"], stats, "he")[0]
    assert wide["meaningful"] and wide["plain"] == "He talks noticeably faster in NPR interviews than in Sunday shows"
    assert wide["may_support"] and not wide["neutral"]
    assert P.contrast_items(members["Few"], stats, "he")[0]["neutral"]
    assert P.contrast_items(members["Floor"], stats, "he")[0]["neutral"]      # floor pairs are never compared
    assert P.contrast_items(members["M20"], stats, "he")[0]["neutral"]


def test_repeated_story_flags_two_sensibilities_that_tell_one_story():
    packets, _ = build()
    ps = packets["Cam Floor"]["public_sensibilities"]
    assert [p["id"] for p in ps] == ["PS.1", "PS.2"] and all(p["repeated_story"] for p in ps)
    assert packets["Beth Thin"]["public_sensibilities"][0]["repeated_story"] is False


def test_audio_floor_item_only_for_floor_only_or_dominant():
    packets, _ = build()
    assert "AU.floor" in {i["id"] for i in packets["Cam Floor"]["evidence"]}
    assert "AU.floor" not in {i["id"] for i in packets["Alex Rich"]["evidence"]}
    assert "AU.volume" not in {i["id"] for i in packets["Alex Rich"]["evidence"]}      # null band: not an item


def test_longform_findings_carry_status_and_era():
    turn = {"turn_id": "t-01", "source_era": "PRE_CURRENT_LEADERSHIP_ROLE", "semantic_review_qualified": False,
            "channel": "Podcast", "recorded_at": "2022-03-24",
            "semantic_findings": [{"finding": "f", "meeting_implication": "m", "scope": "s"}]}
    items = P.longform_items({"speech_profile": {"longform_interview_evidence": {"full_turns": [turn, {**turn, "turn_id": "t-02", "semantic_findings": []}]}}})
    by = {i["id"]: i for i in items}
    assert by["LF.t-01.1"]["status"] == "provisional" and by["LF.t-01.1"]["may_support"] == ["speaks"]
    assert by["LF.t-01.1"]["era"] == "PRE_CURRENT_LEADERSHIP_ROLE" and by["LF.t-01.1"]["source_text"] == "Podcast, 2022"
    assert by["LF.t-02"]["status"] == "no_findings" and by["LF.t-02"]["may_support"] == []
    qualified = P.longform_items({"speech_profile": {"longform_interview_evidence": {"full_turns": [{**turn, "semantic_review_qualified": True,
                                                                                                      "source_era": "CURRENT_ROLE_CONDITIONED"}]}}})
    assert "warning_sign" in qualified[1]["may_support"]
    earlier = P.longform_items({"speech_profile": {"longform_interview_evidence": {"full_turns": [{**turn, "semantic_review_qualified": True}]}}})
    assert earlier[1]["may_support"] == ["personality", "speaks"]                       # a qualified pre-role finding is no Warning sign evidence


def test_leadership_block_marks_the_era_rule():
    block = P.leadership_block({"leadership_audio_source_policy": LEADER})
    assert block["title"] == "Speaker of the House" and block["since_year_text"] == "since 2023"
    assert block["interview_audio_split_by_era"] is False and P.leadership_block({}) is None


# ---- basis ----
def test_basis_counts_dates_and_confidence_are_computed():
    packets, _ = build()
    rich = packets["Alex Rich"]["basis"]
    assert rich["floor_speeches"] == 400 and rich["hearings"] == 30 and rich["longform_recordings"] == 1
    assert (rich["date_from"], rich["date_to"]) == ("2018-05", "2026-10")                  # floor and hearing dates together
    assert rich["confidence"] == "High"
    assert rich["line"] == ("Basis: 400 floor speeches, 30 hearing transcripts, 1 long-form recording (transcript attributed), "
                            "May 2018 to Oct 2026. Confidence: High.")
    assert packets["Beth Thin"]["basis"]["confidence"] == "Low"
    assert packets["Beth Thin"]["basis"]["line"].startswith("Basis: 3 floor speeches, 0 hearing transcripts, 1 long-form recording")
    assert P.compute_basis({**COUNTS["Alex Rich"], "floor_speeches": 1, "hearings": 1}, {"recordings": 0}, "rich")["line"].startswith(
        "Basis: 1 floor speech, 1 hearing transcript, ")
    assert "Jan 2025." in packets["Beth Thin"]["basis"]["line"]                              # one month: no range
    assert packets["Cam Floor"]["basis"]["confidence"] == "Medium"


def test_longform_dates_extend_the_range_only_when_present():
    base = dict(COUNTS["Alex Rich"])
    plain = P.compute_basis(base, {"recordings": 1, "level": None}, "rich")
    wide = P.compute_basis(base, {"recordings": 1, "level": "VOICE_VERIFIED", "date_from": "2010-01-05", "date_to": "2027-02-01"}, "rich")
    assert plain["date_from"] == "2018-05" and wide["date_from"] == "2010-01" and wide["date_to"] == "2027-02"
    assert "(voice verified)" in wide["line"] and "(" not in plain["line"].split("long-form recording")[1].split(",")[0]


def test_basis_rejects_an_unparseable_date():
    with pytest.raises(ValueError):
        P.compute_basis({**COUNTS["Alex Rich"], "floor_from": "March 2019"}, {"recordings": 0}, "rich")


# ---- readiness, departed, partial long-form ----
def test_partial_longform_status_blocks_ready_unless_flagged_for_dry_runs():
    packets, _ = build(levels_doc=levels(status="PARTIAL"))
    assert not any(p["ready_for_writing"] for p in packets.values())
    assert all("not FINAL" in p["not_ready_reasons"][0] for p in packets.values())
    packets, _ = build(levels_doc=levels(status="PARTIAL"), allow_partial_longform=True)
    assert all(p["ready_for_writing"] for p in packets.values())


def test_null_level_is_not_ready_with_the_files_reason_and_no_default_level():
    packets, _ = build(levels_doc=levels(**{"Beth Thin": level("Beth Thin", None, reason="No hard name cue in selected recording")}))
    thin = packets["Beth Thin"]
    assert thin["ready_for_writing"] is False
    assert thin["not_ready_reasons"] == ["long-form level is null: No hard name cue in selected recording"]
    assert packets["Alex Rich"]["ready_for_writing"] is True


def departed_roster(monkeypatch, *rows):
    """The roster module's departed list, replaced: rows are (member, departed_on)."""
    monkeypatch.setattr(P.roster, "departed", lambda root=None: [{"member": m, "departed_on": d} for m, d in rows])


def test_departed_member_is_never_ready(monkeypatch):
    departed_roster(monkeypatch, ("Beth Thin", "2026-07-11"))
    packets, _ = build()
    assert packets["Beth Thin"]["not_ready_reasons"][0] == "departed 2026-07-11" and packets["Beth Thin"]["departed"] is True
    assert packets["Beth Thin"]["ready_for_writing"] is False and packets["Alex Rich"]["departed"] is False


def test_the_live_roster_names_graham_as_departed_on_the_eleventh_of_july_and_defers_nobody():
    assert P.departed_members()["Lindsey Graham"] == "departed 2026-07-11"                                   # roster.departed(), no monkeypatch
    assert not hasattr(P, "DEPARTED") and P.DEFERRED_NEW_MEMBERS == ()


def test_a_ledger_member_named_graham_is_departed_with_the_live_roster():
    led = ledger()
    led["members"] = {"Lindsey Graham": {**led["members"]["Alex Rich"], "name": "Lindsey Graham"}}
    packets, problems = P.build_all(led, legislators=LEGS, congestion=[{"member": "Lindsey Graham", "bioguide": "A1", "chamber": "House"}],
                                    model_bio={}, desk_src={"members": []}, wiki={}, basis_counts={"Lindsey Graham": COUNTS["Alex Rich"]},
                                    levels_doc={"status": "FINAL", "members": {"Lindsey Graham": level("Lindsey Graham")}})
    graham = packets["Lindsey Graham"]
    assert problems == [] and graham["departed"] is True and graham["ready_for_writing"] is False
    assert graham["not_ready_reasons"] == ["departed 2026-07-11"]


def test_a_departed_member_with_no_legislators_row_is_emitted_not_ready_without_a_lookup_failure():
    led = ledger()
    led["members"] = {"Lindsey Graham": {**led["members"]["Alex Rich"], "name": "Lindsey Graham"}, "Alex Rich": led["members"]["Alex Rich"]}
    packets, problems = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src={"members": []}, wiki={}, basis_counts=COUNTS,
                                    levels_doc=levels(**{"Lindsey Graham": level("Lindsey Graham")}))
    graham = packets["Lindsey Graham"]
    assert problems == [] and graham["departed"] is True and graham["ready_for_writing"] is False and graham["not_ready_reasons"][0] == "departed 2026-07-11"
    assert graham["member"]["pronoun"] == "they" and graham["member"]["surname"] == "Graham" and graham["packet_sha256"] == P.content_sha256(graham)
    assert packets["Alex Rich"]["departed"] is False


def test_the_roster_modules_departed_list_names_departed_members(monkeypatch):
    departed_roster(monkeypatch, ("Cam Floor", "2026-08-01"), ("Pablo Hern" + P.FFFD + "ndez Rivera", ""))
    got = P.departed_members()
    assert got == {"Cam Floor": "departed 2026-08-01", "Pablo Hernández Rivera": "departed"}
    packets, problems = build(legislators=[r for r in LEGS if r["bioguide_id"] != "C1"], congestion=[r for r in CONG if r["member"] != "Cam Floor"])
    assert problems == [] and packets["Cam Floor"]["departed"] is True and packets["Cam Floor"]["ready_for_writing"] is False


def test_a_missing_roster_artifact_fails_the_build_closed(monkeypatch):
    def gone(root=None):
        raise FileNotFoundError("data/engine/roster.json")
    monkeypatch.setattr(P.roster, "departed", gone)
    with pytest.raises(FileNotFoundError):
        build()


def test_the_rosters_arrivals_are_thin_members_and_a_pre_congress_only_recording_is_no_cue():
    thin = record(traits={"Extraversion": "below most"}, longform={"full_turns": [{**qualified_turn(True)["full_turns"][0], "source_era": "PRE_CONGRESS_LOCAL_OFFICE_X"}]})
    packet = packet_of(thin, "Alex Rich")
    assert packet["evidence_tier"] == "thin" and packet["warning_form"] == "procedural"
    item = next(i for i in packet["longform"] if i["id"] == "LF.t-01.1")
    assert item["may_support"] == ["personality", "speaks"] and item["era"] == "PRE_CONGRESS_LOCAL_OFFICE_X"
    assert packet["ready_for_writing"] is True and packet["departed"] is False


def test_missing_basis_row_makes_the_member_not_ready():
    packets, _ = build(basis_counts={k: v for k, v in COUNTS.items() if k != "Cam Floor"})
    assert packets["Cam Floor"]["basis"] is None and "no speech basis counts row" in packets["Cam Floor"]["not_ready_reasons"]


def test_unknown_bioguide_fails_closed():
    _, problems = build(legislators=[r for r in LEGS if r["bioguide_id"] != "A1"])
    assert problems and "no legislators.csv row" in problems[0]


# ---- keys ----
def test_replacement_character_keys_are_rejected_unless_explicitly_aliased():
    bad = "Pablo Hern" + P.FFFD + "ndez Rivera"
    assert P.canon_key(bad) == "Pablo Hernández Rivera"                              # explicit alias
    assert P.canon_key("Pablo Hernández Rivera") == "Pablo Hernández Rivera"
    with pytest.raises(ValueError):
        P.canon_key("Someone El" + P.FFFD + "se")                                          # no fuzzy matching
    with pytest.raises(ValueError):
        P.assert_clean_keys({"Fine Name": 1, "Bro" + P.FFFD + "ken": 2})
    with pytest.raises(ValueError):
        P.canon_map({bad: 1, "Pablo Hernández Rivera": 2})                            # alias must not silently merge two rows


def test_real_ledger_has_no_replacement_character_in_any_member_key():
    path = ROOT / "data/members/member_narrative_evidence_v4.json"
    if not path.is_file():
        pytest.skip("ledger not present")
    keys = json.loads(path.read_text(encoding="utf-8"))["members"]
    P.assert_clean_keys(keys)
    assert "Pablo Hernández Rivera" in keys


def test_packet_hash_covers_what_the_writer_saw_and_nothing_else():
    packets, _ = build()
    p = packets["Alex Rich"]
    assert p["packet_sha256"] == P.content_sha256(p) and len(p["packet_sha256"]) == 64
    same = {**p, "avoid_phrases": ["x"], "coined_animals": [{"animal": "Beaver"}], "basis": None, "ready_for_writing": False,
            "not_ready_reasons": ["x"], "reference_only": {}, "longform_level_row": None, "departed": True}
    assert P.content_sha256(same) == p["packet_sha256"]                       # corpus hooks, readiness and Basis are not hashed
    evidence = copy.deepcopy(p["evidence"])
    evidence[0]["band"] = "well above most"
    for key, change in (("evidence_tier", "thin"), ("gates", {"DF": False}), ("suggested_animal", "Owl"),
                        ("member", {**p["member"], "pronoun": "she"}), ("evidence", evidence)):
        assert P.content_sha256({**p, key: change}) != p["packet_sha256"], key


def test_build_contents_matches_build_all_and_needs_no_basis_or_levels():
    inputs = dict(legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={})
    contents, problems = P.build_contents(ledger(), **inputs)
    packets, _ = build()
    assert problems == [] and {n: c["packet_sha256"] for n, c in contents.items()} == {n: p["packet_sha256"] for n, p in packets.items()}
    assert "basis" not in contents["Alex Rich"] and "ready_for_writing" not in contents["Alex Rich"]


# ---- avoid phrases / registry ----
def test_avoid_phrases_collect_six_word_runs_shared_by_many_briefs():
    briefs = {"m%d" % i: {"bottom_line": "bring the one fact that settles the question quickly " + "x%d" % i, "do": [], "dont": []} for i in range(10)}
    got = P.compute_avoid_phrases(briefs)
    assert "bring the one fact that settles" in got and P.compute_avoid_phrases({}) == []


# ---- LD.plain (guide 4), warning_form (guide 7), the coined-animal registry (guide 8.2 step 6, 8.4)
def guide_ld_rows():
    guide = (ROOT / "fec/members/member_briefs_v3_style_guide.md").read_text(encoding="utf-8")
    table = guide.split("| `LD.claim` | `LD.plain` |", 1)[1].split("\n\n", 1)[0]
    return [tuple(c.strip() for c in line.strip("|").split("|")) for line in table.splitlines()[2:] if line.startswith("|")]


def test_ld_plain_matches_every_row_of_the_guide_table_for_all_three_pronouns():
    rows = guide_ld_rows()
    assert len(rows) >= 20 and ("House Democratic Leader", "<pron> party's House leader") in rows
    for claim, plain in rows:
        for pronoun, poss in (("he", "his"), ("she", "her"), ("they", "their")):
            assert P.ld_plain(claim, pronoun) == plain.replace("<pron>", poss), (claim, pronoun)


def test_titles_without_a_party_word_are_used_as_given_and_an_unlisted_party_title_fails_closed():
    for title in ("Speaker of the House", "House Majority Leader", "Senate Majority Whip", "House Majority Whip", "Senate Majority Leader"):
        assert P.ld_plain(title, "he") == title
    for title in ("House Democratic Quartermaster", "Senate Republican Whip Emeritus", "House GOP Leader"):
        with pytest.raises(ValueError):
            P.ld_plain(title, "she")


def test_every_leadership_title_in_the_real_ledger_has_a_party_free_plain():
    path = ROOT / "data/members/member_narrative_evidence_v4.json"
    if not path.is_file():
        pytest.skip("ledger not present")
    titles = {r["leadership_audio_source_policy"]["current_role"] for r in json.loads(path.read_text(encoding="utf-8"))["members"].values()
              if r.get("leadership_audio_source_policy")}
    assert titles
    for title in titles:
        assert not re.search(r"Democrat|Republican|GOP", P.ld_plain(title, "he")), title


def test_a_leader_packet_carries_ld_plain_on_the_item_and_the_leadership_block():
    led = ledger()
    rec = {**led["members"]["Alex Rich"], "name": "Alex Rich", "leadership_audio_source_policy": {**LEADER, "current_role": "House Democratic Leader"}}
    led["members"] = {"Alex Rich": rec}
    packet = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS, levels_doc=levels())[0]["Alex Rich"]
    ld = next(i for i in packet["evidence"] if i["id"] == "LD")
    assert ld["claim"] == "House Democratic Leader" and ld["plain"] == "his party's House leader"
    assert packet["member"]["leadership"]["plain"] == "his party's House leader" and packet["member"]["leadership"]["title"] == "House Democratic Leader"


def packet_of(rec, name="Alex Rich", **kw):
    led = ledger()
    led["members"] = {name: {**rec, "name": name, "chamber": "House"}}
    return P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS,
                       levels_doc=levels(), **kw)[0][name]


def qualified_turn(qualified):
    return {"full_turns": [{"turn_id": "t-01", "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP", "semantic_review_qualified": qualified,
                            "channel": "Podcast", "recorded_at": "2022-03-24",
                            "semantic_findings": [{"finding": "f", "meeting_implication": "m", "scope": "s"}]}]}


def test_warning_form_is_observed_with_a_cue_eligible_item_and_procedural_without():
    packets, _ = build()
    assert {n: p["warning_form"] for n, p in packets.items()} == {"Alex Rich": "observed", "Beth Thin": "observed", "Cam Floor": "observed"}
    assert packet_of(record(traits={"Extraversion": "below most"}, facets={"warmth": "above most"}))["warning_form"] == "procedural"
    assert packet_of(record(measures={"burst": "middling"}, turns=900, audio=audio()))["warning_form"] == "procedural"     # a neutral IX item is no cue


@pytest.mark.parametrize("rec,form", [
    (dict(behavior={"conversational_dominance": "above most"}), "observed"), (dict(behavior={"words_per_turn": "below most"}), "observed"),
    (dict(reads={"agreeableness_low_antagonism": "above most"}), "observed"), (dict(hearing="below most"), "observed"),
    (dict(defense="above most", turns=900, measures={"wpt_mean": "middling"}), "observed"),
    (dict(longform=qualified_turn(True)), "observed"), (dict(longform=qualified_turn(False)), "procedural"),
    (dict(behavior={"legislative_diligence": "above most"}), "procedural"), (dict(facets={"assertiveness": "above most"}), "procedural")])
def test_each_cue_eligible_item_makes_the_warning_form_observed(rec, form):
    assert packet_of(record(**rec))["warning_form"] == form


def test_the_packet_hash_follows_the_warning_form(packet):
    q = copy.deepcopy(packet)
    q["warning_form"] = "procedural" if packet["warning_form"] == "observed" else "observed"
    assert P.content_sha256(q) != packet["packet_sha256"]


SEED = [{"animal": "Beaver", "descriptor": "cooperative and wide-ranging", "image": "builds with others",
         "signature": [["BH.coop", "hi"], ["TR.A", "hi"]], "first_member": "Brian Fitzpatrick"},
        {"animal": "Ox", "descriptor": "practical and narrowly focused", "image": "pulls one furrow at a time",
         "signature": [["TR.O", "lo"], ["BH.breadth", "lo"]], "first_member": "Vince Fong"},
        {"animal": "Hare", "descriptor": "wide-ranging and loose", "image": "covers a lot of ground",
         "signature": [["BH.breadth", "hi"], ["BH.diligence", "lo"], ["RC", "less"]], "first_member": "Linda S\u00e1nchez"}]


def mini_packet(items, *, coin, suggested=None, matched=0, rc=None, tier="standard"):
    ev = [it(i, b) for i, b in items]
    if rc:
        ev.append({"id": "RC", "band": None, "neutral": False, "direction": rc})
    return {"evidence": ev, "gates": GATES, "appeals": None, "public_sensibilities": [], "evidence_tier": tier,
            "suggested_animal": suggested, "archetype_candidates": [{"animal": suggested, "matched": matched}] if suggested else [],
            "coin_recommended": {"value": coin, "template_coin": coin, "reason": "r", "binding_unexplained": {}, "advisory_unexplained": {},
                                 "registry_match": None}}


def test_a_signature_holds_in_full_with_band_receptivity_and_present_conditions():
    view = P._View([it("BH.coop", ABOVE), it("TR.A", TOP), it("BH.breadth", WELL_BELOW)], {**GATES, "DF": True}, [], [], "less")
    assert P.signature_holds([["BH.coop", "hi"], ["TR.A", "hi"]], view) and P.signature_holds([["TR.A", "HI"], ["RC", "less"]], view)
    assert not P.signature_holds([["BH.coop", "HI"]], view) and not P.signature_holds([["RC", "more"]], view)
    assert P.signature_holds([["DF", "present"], ["BH.breadth", "LO"]], view) and not P.signature_holds([["TR.C", "hi"]], view)
    assert not P.signature_holds([], view)                                                         # an empty signature matches nothing


def test_the_registry_match_is_the_entry_with_the_most_signature_items_then_alphabetical():
    registry = SEED + [{"animal": "Aardvark", "descriptor": "d", "image": "i", "signature": [["BH.coop", "hi"], ["TR.A", "hi"]], "first_member": "x"},
                       {"animal": "Zebra", "descriptor": "z", "image": "i", "signature": [["BH.coop", "hi"], ["TR.A", "hi"], ["TR.E", "lo"]], "first_member": "y"}]
    view = P._View([it("BH.coop", ABOVE), it("TR.A", ABOVE)], GATES, [], [], None)
    assert P.registry_match(registry, view) == {"animal": "Aardvark", "descriptor": "d"}                  # tie on two items: alphabetical
    view = P._View([it("BH.coop", ABOVE), it("TR.A", ABOVE), it("TR.E", BELOW)], GATES, [], [], None)
    assert P.registry_match(registry, view) == {"animal": "Zebra", "descriptor": "z"}                     # three items beat two
    assert P.registry_match(registry, P._View([it("BH.coop", ABOVE)], GATES, [], [], None)) is None


def test_the_registry_is_checked_for_a_coin_and_for_a_thin_template_animal_under_three_slots():
    cooperative = [("BH.coop", ABOVE), ("TR.A", ABOVE)]
    coin = P.apply_registry(mini_packet(cooperative, coin=True), SEED)
    assert coin["coin_recommended"]["registry_match"]["animal"] == "Beaver" and coin["coin_recommended"]["value"] is True
    kept = P.apply_registry(mini_packet(cooperative, coin=False, suggested="Owl", matched=3), SEED)             # a template animal with three slots stands
    assert kept["coin_recommended"]["registry_match"] is None and kept["coin_recommended"]["value"] is False
    thin = P.apply_registry(mini_packet(cooperative, coin=False, suggested="Owl", matched=2, tier="thin"), SEED)  # fewer than three: registry first
    assert thin["coin_recommended"]["registry_match"]["animal"] == "Beaver" and thin["coin_recommended"]["value"] is True
    assert thin["coin_recommended"]["template_coin"] is False and thin["suggested_animal"] == "Owl"
    none = P.apply_registry(mini_packet([("TR.C", ABOVE)], coin=True), SEED)
    assert none["coin_recommended"]["registry_match"] is None and none["coin_recommended"]["value"] is True


def test_applying_the_registry_never_changes_the_packet_hash(packet):
    p = copy.deepcopy(packet)
    p["coin_recommended"].update(value=True, template_coin=True)
    p["packet_sha256"] = P.content_sha256(p)
    newt = [{"animal": "Newt", "descriptor": "d", "image": "i", "signature": [["TR.A", "lo"], ["BH.diligence", "hi"]], "first_member": "x"}]
    P.apply_registry(p, newt)
    assert p["coin_recommended"]["registry_match"]["animal"] == "Newt" and P.content_sha256(p) == p["packet_sha256"]
    thin = copy.deepcopy(packet)                                               # a clear call whose template animal has two slots: the registry flips ``value``
    thin["coin_recommended"].update(value=False, template_coin=False)
    thin["archetype_candidates"] = [{**c, "matched": 2} if c["animal"] == thin["suggested_animal"] else c for c in thin["archetype_candidates"]]
    thin["packet_sha256"] = P.content_sha256(thin)
    P.apply_registry(thin, newt)
    assert thin["coin_recommended"]["value"] is True and P.content_sha256(thin) == thin["packet_sha256"]


def test_build_all_attaches_the_registry_and_fills_registry_match_for_a_coin():
    rec = record(traits={"Agreeableness": "among the highest in the chamber"}, behavior={"cross_party_cooperation": "among the highest in the chamber"})
    plain = packet_of(rec)
    assert plain["coin_recommended"]["value"] is True and plain["coin_recommended"]["registry_match"] is None
    matched = packet_of(rec, coined=SEED)
    assert matched["coin_recommended"]["registry_match"] == {"animal": "Beaver", "descriptor": "cooperative and wide-ranging"}
    assert [e["animal"] for e in matched["coined_animals"]] == ["Beaver", "Ox", "Hare"] and matched["packet_sha256"] == plain["packet_sha256"]


def test_the_exemplars_file_seeds_the_registry_in_the_guide_shape():
    doc = json.loads((ROOT / "fec/members/member_briefs_v3_exemplars.json").read_text(encoding="utf-8"))
    seed = P.registry_seed(doc)
    assert [e["animal"] for e in seed] == ["Beaver", "Ox", "Hare"]
    assert all(set(e) >= {"animal", "descriptor", "image", "signature", "first_member"} and len(e["signature"]) >= 2 for e in seed)
    with pytest.raises(ValueError):
        P.registry_seed({"coined_registry": [{"animal": "Mole", "descriptor": "d", "image": "i", "signature": [["TR.A", "sideways"], ["TR.E", "hi"]], "first_member": "x"}]})


IMAGES = {"Badger": "a badger that holds its ground", "Mole": "a mole that works unseen below ground"}


def new_coin_brief(animal="Badger", refs=("TR.A", "BH.diligence", "RC", "MA")):
    b = copy.deepcopy(good_brief())
    b["archetype"].update(animal=animal, descriptor="blunt and thorough", justification="No template animal fits a member who is this blunt, thorough and hard to move.")
    b["evidence_refs"]["archetype"] = list(refs)
    return b


def test_a_new_coin_registers_with_the_outer_band_ids_it_cited_plus_receptivity():
    packets, _ = build()
    grown = P.grow_registry(SEED, {"Alex Rich": new_coin_brief()}, packets, images=IMAGES)
    assert [e["animal"] for e in grown] == ["Beaver", "Ox", "Hare", "Badger"]
    badger = grown[-1]
    assert badger["signature"] == [["TR.A", "LO"], ["BH.diligence", "HI"], ["RC", "less"]]     # TR.A well below, BH.diligence among the highest, pressure moves him less
    assert badger["first_member"] == "Alex Rich" and badger["descriptor"] == "blunt and thorough" and badger["image"] == IMAGES["Badger"]
    assert len(SEED) == 3                                                                         # the seed is not mutated


def test_a_coin_registers_only_with_two_or_more_outer_signature_items():
    packets, _ = build()
    one = P.grow_registry(SEED, {"Alex Rich": new_coin_brief(refs=("TR.C", "BH.diligence", "MA"))}, packets, images=IMAGES)       # TR.C is only "above most"
    assert [e["animal"] for e in one] == ["Beaver", "Ox", "Hare"]


def test_template_animals_known_names_and_unknown_members_do_not_register():
    packets, _ = build()
    briefs = {"Alex Rich": new_coin_brief(animal="Bull"), "Beth Thin": new_coin_brief(animal="Beaver"), "Ghost": new_coin_brief(animal="Mole")}
    assert [e["animal"] for e in P.grow_registry(SEED, briefs, packets, images=IMAGES)] == ["Beaver", "Ox", "Hare"]
    twice = P.grow_registry(SEED, {"Alex Rich": new_coin_brief(), "Cam Floor": new_coin_brief(refs=("FC.warmth", "AU.pace", "MA"))}, packets, images=IMAGES)
    assert [e["animal"] for e in twice].count("Badger") == 1 and twice[-1]["first_member"] == "Alex Rich"          # the first use keeps the entry


def test_build_all_grows_the_registry_from_finished_briefs_before_matching():
    led = ledger()
    packets, _ = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS,
                             levels_doc=levels(), coined=SEED, finished={"Alex Rich": new_coin_brief()}, registry_images=IMAGES)
    assert [e["animal"] for e in packets["Cam Floor"]["coined_animals"]][-1] == "Badger"
    assert packets["Alex Rich"]["packet_sha256"] == build()[0]["Alex Rich"]["packet_sha256"]


def test_the_distribution_names_registry_matches():
    packets = {"a": {"suggested_animal": "Bull", "coin_recommended": {"value": False, "registry_match": None}},
               "b": {"suggested_animal": None, "coin_recommended": {"value": True, "registry_match": {"animal": "Ox"}}},
               "c": {"suggested_animal": "Owl", "coin_recommended": {"value": True, "registry_match": None}}}
    assert P.distribution(packets) == {"Bull": 1, "Ox (registry)": 1, "COIN": 1}


# ---- long-form source era (diagnostics file) and its gating
def lf_turn(tid="t-01", era="NOT_CURRENT_FORMAL_LEADERSHIP", qualified=True, source_id="src-1", findings=1):
    return {"turn_id": tid, "source_id": source_id, "source_era": era, "semantic_review_qualified": qualified, "channel": "Podcast",
            "recorded_at": "2022-03-24", "semantic_findings": [{"finding": "f%d" % n, "meeting_implication": "m", "scope": "s"} for n in range(findings)]}


def lf_rec(*turns):
    return {"speech_profile": {"longform_interview_evidence": {"full_turns": list(turns)}}}


@pytest.mark.parametrize("era,restricted", [
    ("PRE_CONGRESS_LOCAL_OFFICE_GWINNETT_SCHOOL_BOARD", True), ("PRE_CONGRESS_STATE_LEGISLATURE", True), ("PRE_CURRENT_LEADERSHIP_ROLE", True),
    ("CURRENT_ROLE_CONDITIONED", False), ("NOT_CURRENT_FORMAL_LEADERSHIP", False), ("", False), (None, False)])
def test_a_pre_congress_or_pre_role_era_is_restricted(era, restricted):
    assert P.era_restricted(era) is restricted


def test_a_restricted_era_item_may_support_speaks_and_personality_only():
    qualified = P.longform_items(lf_rec(lf_turn(era="PRE_CONGRESS_LOCAL_OFFICE_X")))[1]
    assert qualified["status"] == "qualified" and qualified["era"] == "PRE_CONGRESS_LOCAL_OFFICE_X" and qualified["may_support"] == ["personality", "speaks"]
    role = P.longform_items(lf_rec(lf_turn(era="PRE_CURRENT_LEADERSHIP_ROLE")))[1]
    assert role["may_support"] == ["personality", "speaks"]
    current = P.longform_items(lf_rec(lf_turn(era="CURRENT_ROLE_CONDITIONED")))[1]
    assert {"archetype", "do", "dont", "warning_sign", "follow_up"} <= set(current["may_support"]) and "personality" not in current["may_support"]
    provisional = P.longform_items(lf_rec(lf_turn(era="PRE_CONGRESS_LOCAL_OFFICE_X", qualified=False)))[1]
    assert provisional["status"] == "provisional" and provisional["may_support"] == ["speaks"]          # provisional never gains a field
    for field in ("archetype", "do", "dont", "warning_sign"):
        assert field not in qualified["may_support"] and field not in provisional["may_support"]


def test_the_diagnostics_era_replaces_the_ledger_era_by_recording_id_or_for_the_whole_member():
    records = [{"ids": {"src-1"}, "source_era": "PRE_CONGRESS_LOCAL_OFFICE_X", "recorded_at": "2021-02-12"}]
    turns = (lf_turn("t-01", source_id="src-1"), lf_turn("t-02", source_id="src-2"))
    by = {i["id"]: i for i in P.longform_items(lf_rec(*turns), records)}
    assert by["LF.t-01"]["era"] == "PRE_CONGRESS_LOCAL_OFFICE_X" and by["LF.t-01"]["era_source"] == "diagnostics" and by["LF.t-01.1"]["may_support"] == ["personality", "speaks"]
    assert by["LF.t-02"]["era"] == "NOT_CURRENT_FORMAL_LEADERSHIP" and by["LF.t-02"]["era_source"] == "ledger" and "warning_sign" in by["LF.t-02.1"]["may_support"]
    assert by["LF.t-01"]["turn_id"] == "t-01" and by["LF.t-01"]["source_id"] == "src-1"
    wide = P.longform_items(lf_rec(*turns), [{"ids": set(), "source_era": "CURRENT_ROLE_CONDITIONED", "recorded_at": None}])
    assert {i["era"] for i in wide} == {"CURRENT_ROLE_CONDITIONED"}                                     # a record with no id speaks for every recording of the member
    both = P.longform_items(lf_rec(*turns), [{"ids": set(), "source_era": "CURRENT_ROLE_CONDITIONED"}, {"ids": {"t-02"}, "source_era": "PRE_CONGRESS_X"}])
    assert {i["id"]: i["era"] for i in both if "." not in i["id"][3:]} == {"LF.t-01": "CURRENT_ROLE_CONDITIONED", "LF.t-02": "PRE_CONGRESS_X"}   # an id match wins


def test_the_diagnostics_reader_accepts_the_three_shapes_and_aliases_member_keys(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({
        "A": {"rec-1": {"source_id": "src-1", "source_era": "E1", "recorded_at": "2021-02-12"}, "rec-2": {"source_era": "E2"}},
        "B": [{"turn_id": "t-9", "source_era": "E3"}],
        "C": {"source_era": "E4", "recorded_at": "2021-02-12", "job": "c__abc"},
        "D": {"tstatus": "LOCAL_ASR_READY", "wavstate": "OK"},                                              # no era: nothing to say
        "E": {"rec-1": {"source_id": "src-1", "recorded_at": "2021-02-12"}},                                # a recording with no era says nothing either
        "Pablo Hern" + P.FFFD + "ndez Rivera": {"source_era": "E5"}}), encoding="utf-8")
    got = P.read_longform_diagnostics(f)
    assert got["A"] == [{"ids": {"rec-1", "src-1"}, "source_era": "E1", "recorded_at": "2021-02-12"}, {"ids": {"rec-2"}, "source_era": "E2", "recorded_at": None}]
    assert got["B"][0]["ids"] == {"t-9"} and got["C"][0]["ids"] == {"c__abc"} and "D" not in got and "E" not in got
    assert got["Pablo Hern\u00e1ndez Rivera"][0]["source_era"] == "E5"
    f.write_text(json.dumps(["not", "a", "map"]), encoding="utf-8")
    with pytest.raises(SystemExit):
        P.read_longform_diagnostics(f)
    f.write_text(json.dumps({"A": "pre-congress"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        P.read_longform_diagnostics(f)


def test_a_restricted_qualified_finding_is_no_cue_for_the_warning_sign():
    assert packet_of(record(longform=qualified_turn(True)))["warning_form"] == "observed"
    restricted = {"full_turns": [{**qualified_turn(True)["full_turns"][0], "source_era": "PRE_CURRENT_LEADERSHIP_ROLE"}]}
    assert packet_of(record(longform=restricted))["warning_form"] == "procedural"
    assert "LF.t-01.1" not in P.cue_eligible_ids([], GATES, P.longform_items(lf_rec(lf_turn(era="PRE_CONGRESS_X"))))
    assert "LF.t-01.1" in P.cue_eligible_ids([], GATES, P.longform_items(lf_rec(lf_turn(era="CURRENT_ROLE_CONDITIONED"))))


def test_build_all_applies_the_diagnostics_before_hashing_and_the_warning_form_follows_it():
    rec = record(longform=qualified_turn(True))
    plain = packet_of(rec)
    assert plain["warning_form"] == "observed"
    eras = {"Alex Rich": [{"ids": {"t-01"}, "source_era": "PRE_CONGRESS_LOCAL_OFFICE_X", "recorded_at": "2021-02-12"}]}
    packet = packet_of(rec, longform_eras=eras)
    item = next(i for i in packet["longform"] if i["id"] == "LF.t-01.1")
    assert item["era"] == "PRE_CONGRESS_LOCAL_OFFICE_X" and packet["warning_form"] == "procedural"
    assert packet["packet_sha256"] == P.content_sha256(packet) != plain["packet_sha256"]                    # the era is part of what the writer saw
    inputs = dict(legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={})
    led = ledger()
    led["members"] = {"Alex Rich": {**rec, "name": "Alex Rich", "chamber": "House"}}
    assert P.build_contents(led, longform_eras=eras, **inputs)[0]["Alex Rich"]["packet_sha256"] == packet["packet_sha256"]
    assert P.build_contents(led, **inputs)[0]["Alex Rich"]["packet_sha256"] == plain["packet_sha256"]       # the desk, without the file, sees the ledger era


# ---- pilot 2 review: avoid_phrases, registry theme cover and image, BIO filter
GUIDE = ROOT / "fec/members/member_briefs_v3_style_guide.md"
REVIEW_B_SEED_ADDITIONS = ["is his home state", "is her home state", "answers run before you respond", "a weaker suit for",
                           "reach him before his view", "reach her before her view", "hearing manner as", "lands whole",
                           "crowd his sentences", "crowd her sentences", "gets to him more than", "gets to her more than"]


def guide_seed_phrases():
    line = next(l for l in GUIDE.read_text(encoding="utf-8").splitlines() if l.startswith("Seed `avoid_phrases`"))
    return re.findall(r'"([^"]+)"', line)


def test_the_seed_avoid_phrases_hold_every_guide_seed_line_and_review_bs_additions():
    guide = guide_seed_phrases()
    assert len(guide) >= 20 and set(guide) <= set(P.SEED_AVOID_PHRASES)
    assert set(REVIEW_B_SEED_ADDITIONS) <= set(P.SEED_AVOID_PHRASES) and len(set(P.SEED_AVOID_PHRASES)) == len(P.SEED_AVOID_PHRASES)


def test_avoid_phrases_are_always_a_list_that_starts_with_the_seed_lines():
    for avoid in ((), None, ["extra one two three four five"]):
        packets, _ = build(avoid=avoid)
        for p in packets.values():
            assert isinstance(p["avoid_phrases"], list) and p["avoid_phrases"][:len(P.SEED_AVOID_PHRASES)] == list(P.SEED_AVOID_PHRASES)
            assert len(set(p["avoid_phrases"])) == len(p["avoid_phrases"])
    assert packets["Alex Rich"]["avoid_phrases"][-1] == "extra one two three four five"
    assert build(avoid=P.SEED_AVOID_PHRASES[:3])[0]["Alex Rich"]["avoid_phrases"] == list(P.SEED_AVOID_PHRASES)       # a seed line is not listed twice


def binding_packet(items, binding):
    packet = mini_packet(items, coin=True)
    packet["coin_recommended"]["binding_unexplained"] = binding
    return packet


def test_a_registry_entry_must_have_a_signature_item_in_every_binding_theme():
    luna = [("TR.A", BOTTOM), ("QR.antagonism", BOTTOM), ("TR.O", BELOW), ("BH.breadth", BELOW)]
    ox = SEED[1]                                                                  # TR.O lo, BH.breadth lo: holds, but has nothing combative
    result = P.apply_registry(binding_packet(luna, {"combative": ["TR.A", "QR.antagonism"]}), [ox])
    assert result["coin_recommended"]["registry_match"] is None and result["coin_recommended"]["value"] is True
    fighter = {"animal": "Mastiff", "descriptor": "blunt and narrow", "image": "guards one gate and nothing else", "first_member": "x",
               "signature": [["TR.A", "LO"], ["BH.breadth", "lo"]]}
    result = P.apply_registry(binding_packet(luna, {"combative": ["TR.A", "QR.antagonism"]}), [ox, fighter])
    assert result["coin_recommended"]["registry_match"]["animal"] == "Mastiff"
    free = P.apply_registry(binding_packet(luna, {}), [ox])                         # no binding theme (no animal reached the threshold): the old rule
    assert free["coin_recommended"]["registry_match"]["animal"] == "Ox"


def test_every_binding_theme_needs_cover_not_just_one():
    items = [("BH.coop", TOP), ("TR.A", TOP), ("TR.E", TOP), ("FC.excitement", TOP)]
    both = binding_packet(items, {"cooperative": ["BH.coop", "TR.A"], "expressive": ["TR.E", "FC.excitement"]})
    assert P.apply_registry(both, [SEED[0]])["coin_recommended"]["registry_match"] is None             # Beaver covers only the cooperative theme
    beaver_and_voice = {"animal": "Parrot", "descriptor": "d", "image": "talks with the whole flock", "first_member": "x",
                        "signature": [["BH.coop", "hi"], ["TR.A", "hi"], ["TR.E", "hi"]]}
    both = binding_packet(items, {"cooperative": ["BH.coop", "TR.A"], "expressive": ["TR.E", "FC.excitement"]})
    assert P.apply_registry(both, [SEED[0], beaver_and_voice])["coin_recommended"]["registry_match"]["animal"] == "Parrot"


def test_a_new_coin_registers_only_with_a_plain_short_image_set_at_registration():
    packets, _ = build()
    brief = new_coin_brief()
    image = "a badger that holds its ground"
    pending = []
    assert P.image_problem(image) is None and [e["animal"] for e in P.grow_registry(SEED, {"Alex Rich": brief}, packets, images={"Badger": image})][-1] == "Badger"
    grown = P.grow_registry(SEED, {"Alex Rich": brief}, packets, images={"Badger": image})
    assert grown[-1]["image"] == image and grown[-1]["image"] != brief["archetype"]["justification"]
    unnamed = P.grow_registry(SEED, {"Alex Rich": brief}, packets, pending=pending)                  # no image from the orchestrator yet: not offered to anyone
    assert [e["animal"] for e in unnamed] == ["Beaver", "Ox", "Hare"] and [x["animal"] for x in pending] == ["Badger"] and "image" in pending[0]["reason"]
    for bad in ("badger", "digs in", brief["archetype"]["justification"], "a badger that digs in and holds its ground in every single weather", ""):
        assert P.image_problem(bad), bad
        with pytest.raises(ValueError):
            P.grow_registry(SEED, {"Alex Rich": brief}, packets, images={"Badger": bad})
    assert P.image_problem("a badger that digs in", justification="A badger that digs in") and P.image_problem("a badger that digs in") is None
    assert P.image_problem("three plain words") is None and P.image_problem("one two three four five six seven eight") is None
    assert P.image_problem("one two three four five six seven eight nine") and P.image_problem("one two")


def test_the_image_rule_is_reported_for_the_seed_without_blocking_it():
    assert P.registry_image_problems(SEED) == []
    long_seed = [{**SEED[2], "image": "covers a lot of ground and moves on before the finish"}]
    assert [x["animal"] for x in P.registry_image_problems(long_seed)] == ["Hare"]
    assert P.registry_seed({"coined_registry": long_seed})[0]["animal"] == "Hare"


def test_bio_party_and_district_are_never_citable_and_state_stays_personality_only():
    bio = {"party": "Republican", "state": "Ohio", "district": "4th district", "committees": ["Senate Small Business"],
           "prior_career": None, "first_elected_text": "serving since 2019"}
    by = {i["id"]: i for i in P.bio_items(bio)}
    assert by["BIO.party"]["may_support"] == [] and by["BIO.district"]["may_support"] == []
    assert by["BIO.state"]["may_support"] == ["decides", "personality"] and by["BIO.committees"]["may_support"] == ["decides", "personality"]
    P.audit_bio_items(list(by.values()))
    by["BIO.party"]["may_support"] = ["personality"]
    with pytest.raises(ValueError):
        P.audit_bio_items(list(by.values()))


@pytest.mark.parametrize("text,kept", [
    ("She worked as a nurse for twelve years before she ran for office.", "She worked as a nurse for twelve years before she ran for office."),
    ("He served as a county prosecutor.", "He served as a county prosecutor."),
    ("He was a high school teacher and coach.", "He was a high school teacher and coach."),
    ("She practiced law in Tulsa.", "She practiced law in Tulsa."),
    ("He worked as a lawyer. He later ran for the House.", "He worked as a lawyer."),                           # one complete sentence: the first
    ("]] In 1974 he worked as a lawyer.", None), ("He worked as a lawyer [[in Ohio]].", None), ("He {{worked}} as a lawyer.", None),
    ("Upon her swearing-in, Rep. Grijalva worked as a teacher.", None), ("As Sen. Smith he worked as a lawyer.", None),
    ("He worked as a Republican staffer.", None), ("She worked as a Democratic organizer.", None), ("He worked as a Libertarian activist.", None),
    ("She served as a mayor and later voted for the bill.", None), ("His son died in 1999 and he was a teacher.", None),
    ("He lived in Ohio for most of his life.", None), ("",  None), (None, None),
    ("He worked as a lawyer " + "and a teacher " * 12 + "in the town.", None)])                                  # over 30 words
def test_prior_career_keeps_only_one_clean_job_sentence(text, kept):
    assert P.clean_prior_career(text) == kept


def test_a_prior_career_that_slips_past_the_filter_fails_the_packet_build_closed(monkeypatch):
    wiki = {"Alex Rich": {"status": "ok", "summary": {"overview": "", "career": "Rep. Rich worked as a lawyer and voted often."}}}
    inputs = dict(legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK)
    contents, problems = P.build_contents(ledger(), wiki=wiki, **inputs)
    assert problems == [] and not [i for i in contents["Alex Rich"]["evidence"] if i["id"] == "BIO.prior_career"]        # the filter drops it
    monkeypatch.setattr(P, "clean_prior_career", lambda text: text)                                                 # a filter that stopped working
    contents, problems = P.build_contents(ledger(), wiki=wiki, **inputs)
    assert "Alex Rich" not in contents and any(p.startswith("Alex Rich:") and "prior_career" in p for p in problems)


# ---- cli: fail closed ----
def test_main_fails_closed_when_basis_counts_are_missing(tmp_path, capsys):
    rc = P.main(["--basis-counts", str(tmp_path / "nope.csv"), "--longform-levels", str(tmp_path / "nolevels.json"),
                 "--out", str(tmp_path / "o.json"), "--receipt", str(tmp_path / "r.json")])
    assert rc == 2 and "missing required input" in capsys.readouterr().err and not (tmp_path / "o.json").exists()


def test_read_basis_counts_validates_columns_and_aliases_keys(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("member,crec_count\nA,1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        P.read_basis_counts(f)
    with f.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["member", "crec_count", "crec_min_date", "crec_max_date", "chrg_count", "chrg_min_date", "chrg_max_date"])
        w.writerow(["Pablo Hern" + P.FFFD + "ndez Rivera", 5, "2020-01-02", "2020-02-03", 0, "", ""])
    got = P.read_basis_counts(f)
    assert got["Pablo Hernández Rivera"]["floor_speeches"] == 5 and got["Pablo Hernández Rivera"]["hearing_from"] is None


# --------------------------------------------------------------------------------- validator ----
@pytest.fixture(autouse=True)
def _fresh_example_cache():
    for cached in (V.example_grams, V.example_texts, V.example_rows):
        cached.cache_clear()
    yield
    for cached in (V.example_grams, V.example_texts, V.example_rows):
        cached.cache_clear()


def good_brief():
    """An original brief for the "Alex Rich" fixture (never gold or prompt-example text: the example-copy check would flag it)."""
    return {
        "archetype": {"animal": "Bull", "descriptor": "skeptical and disciplined",
                      "justification": "Winning him over takes real work: he takes charge of a conversation, defends his ground, and finishes more of his work than most members do."},
        "bottom_line": "Walk in with a short, fact-heavy case and expect him to test it. A stronger argument earns more from him than extra pressure does.",
        "do": ["Name the decision you need up front", "Cite a checkable source for every claim you make",
               "Trim your answers to match his brisk, filler-free style", "Meet each objection head on, one at a time"],
        "dont": ["Count on follow-up pressure to wear him down", "Open with small talk or filler",
                 "Assume his mild hearing manner means agreement"],
        "warning_sign": "If he quits asking about your case and keeps restating his usual view, the room has turned into a contest. Hold back new points, offer your single best fact, and promise a written answer to his objection.",
        "follow_up": "Send a one-page memo within a day that addresses every objection he raised, with a source beside every fact.",
        "personality": "Rich is quite skeptical and takes some convincing. He is also fairly careful and organized, and few match his record for votes cast and bills carried through. Describing his work, he leans on exact, technical wording well beyond what most members use. He ranks among the most visible members of the chamber. Hearings bring out a mild side, and he is among the least combative there.",
        "speaks": "He answers briefly and cleanly, with almost no filler words, fewer than nearly anyone in the House. He talks faster than most members, and his pitch swings wider than theirs, so even brief replies have life in them. Once a conversation starts, he takes charge of it. Challenge him and he defends his position, show after show. His word choice carries little emotional charge, so the pressure comes from his habits, not his vocabulary.",
        "decides": "Evidence persuades him; sentiment does not. Lay out what the policy changes and prove each effect. Outside pressure matters less to him than to most members, so the quality of your case carries the weight. Once he takes a stand he holds it, so lay out your full case first. A bill he backs usually gets passed along, so his yes means something.",
        "evidence_refs": {
            "archetype": ["TR.A", "BH.dominance", "DF", "BH.diligence", "BH.wpt", "MA"], "bottom_line": ["MA", "DF", "RC"],
            "do.1": ["MA", "BH.wpt"], "do.2": ["MA", "TR.A"], "do.3": ["BH.wpt", "IX.discmark"], "do.4": ["DF", "IX.defense"],
            "dont.1": ["RC"], "dont.2": ["IX.discmark", "MA"], "dont.3": ["HA", "TR.A"],
            "warning_sign": ["DF", "IX.defense", "MA"], "follow_up": ["DF", "MA"],
            "personality": ["TR.A", "TR.C", "BH.diligence", "QR.diligence", "VI.observed", "HA"],
            "speaks": ["BH.wpt", "IX.wpt_mean", "IX.discmark", "AU.pace", "AU.pitch", "BH.dominance", "DF", "QR.affect"],
            "decides": ["MA", "RC", "DF", "BH.diligence"]}}


@pytest.fixture(scope="module")
def packet():
    packets, _ = build()
    return packets["Alex Rich"]


def kinds(brief, packet):
    issues, _ = V.validate_brief(brief, packet)
    return {(i["field"], i["kind"]) for i in issues}


def test_a_good_brief_passes(packet):
    issues, _ = V.validate_brief(good_brief(), packet)
    assert issues == [], issues


def edit(**changes):
    b = copy.deepcopy(good_brief())
    for key, value in changes.items():
        b[key] = value
    return b


def test_schema_counts_and_missing_ref_keys_fail(packet):
    b = good_brief()
    b["do"] = b["do"][:3]
    assert kinds(b, packet) == {("*", "schema")}
    b = good_brief()
    del b["evidence_refs"]["do.4"]
    assert kinds(b, packet) == {("evidence_refs", "schema")}
    b = good_brief()
    b["extra"] = 1
    assert kinds(b, packet) == {("*", "schema")}


def test_word_bands_fail_and_thin_tier_gets_shorter_floors(packet):
    assert ("bottom_line", "length") in kinds(edit(bottom_line="Bring facts."), packet)
    assert ("personality", "length") in kinds(edit(personality="Rich is skeptical by default."), packet)
    thin = copy.deepcopy(packet)
    thin["evidence_tier"] = "thin"
    short = edit(decides=" ".join(["Results and facts move him more than warm talk does in a short meeting."] * 4))   # 56 words
    assert ("decides", "length") in kinds(short, packet) and ("decides", "length") not in kinds(short, thin)


def test_bullets_forbid_period_negation_and_bad_openers(packet):
    for bad in ("Put the decision first.", "Don't lean on pressure at all", "Never repeat the ask again today", "The ask comes first today"):
        b = good_brief()
        b["dont"][0] = bad
        assert ("dont.1", "form") in kinds(b, packet), bad
    b = good_brief()
    b["do"][0] = "Put the decision you need first; then stop"
    assert ("do.1", "form") in kinds(b, packet)


def test_digits_dashes_markup_and_labels_fail(packet):
    assert ("follow_up", "numbers") in kinds(edit(follow_up=good_brief()["follow_up"] + " Use 3 sources."), packet)
    assert ("follow_up", "dash") in kinds(edit(follow_up=good_brief()["follow_up"].replace(", with", " — with")), packet)
    assert ("follow_up", "form") in kinds(edit(follow_up='Send a short memo within a day with "one" clear fact on every objection he raised, point by point.'), packet)
    assert ("bottom_line", "form") in kinds(edit(bottom_line="Bottom line: " + good_brief()["bottom_line"]), packet)


@pytest.mark.parametrize("phrase", ["He stays calm in the room", "He is a steady presence", "The ledger shows that", "He is probably tough", "No data on that",
                                    "It is a landscape of ideas", "He is above most members", "Trigger words matter here"])
def test_banned_words_fail_in_any_field(packet, phrase):
    got = kinds(edit(speaks=good_brief()["speaks"] + " " + phrase + "."), packet)
    assert ("speaks", "banned_phrase") in got or ("speaks", "gate_violation") in got


def test_banned_lists_are_imported_from_v2_not_copied():
    from fec.members import revise_member_narratives_v2 as R
    assert set(R.BANNED_PRIVATE_STATE) <= V.PRIVATE_STATE and set(R.BANNED_UNSUPPORTED) <= V.UNSUPPORTED
    assert set(R.BANNED_JARGON) <= V.JARGON
    assert {"neuroticism", "trauma"} <= V.PRIVATE_STATE and "attention-seeking" in V.UNSUPPORTED


def test_gates_block_defense_and_cross_party_language_without_their_gate(packet):
    thin = build()[0]["Beth Thin"]
    assert thin["gates"]["DF"] is False
    b = copy.deepcopy(good_brief())
    b["warning_sign"] = b["warning_sign"].replace("keeps restating his usual view", "starts defending his usual view")
    assert b["warning_sign"] != good_brief()["warning_sign"]
    got = kinds(b, thin)
    assert ("warning_sign", "gate_violation") in got and ("speaks", "gate_violation") in got
    cross = edit(decides=good_brief()["decides"] + " He works across the aisle with both parties.")
    assert ("decides", "gate_violation") in kinds(cross, packet)                      # Alex Rich has no BH.coop gate
    assert ("decides", "gate_violation") in kinds(edit(decides=good_brief()["decides"] + " That is a sore spot for him."), packet)


def test_surname_exactly_once_at_the_start_of_personality(packet):
    assert ("personality", "name_use") in kinds(edit(personality=good_brief()["personality"].replace("Rich is", "He is", 1)), packet)
    assert ("personality", "name_use") in kinds(edit(personality=good_brief()["personality"] + " Rich also votes often."), packet)
    assert ("bottom_line", "name_use") in kinds(edit(bottom_line="Bring Rich a tight case backed by facts and expect pushback on it, because a stronger case moves him."), packet)
    assert ("follow_up", "name_use") in kinds(edit(follow_up="Send Alex Rich a short memo within a day that answers each objection raised, point by point."), packet)
    assert ("bottom_line", "name_use") in kinds(edit(bottom_line="Sen. " + good_brief()["bottom_line"]), packet)


def test_pronoun_must_match_but_a_public_story_may_name_a_relative(packet):
    got = kinds(edit(bottom_line=good_brief()["bottom_line"].replace("him", "her")), packet)
    assert ("bottom_line", "pronoun") in got
    b = good_brief()
    b["evidence_refs"]["personality"] = b["evidence_refs"]["personality"] + ["PS.1"]
    story = build()[0]["Alex Rich"]
    story["public_sensibilities"] = [{"id": "PS.1", "may_support": ["personality"]}]
    b["personality"] = good_brief()["personality"] + " His mother lost her job once."
    issues, warnings = V.validate_brief(b, story)
    assert ("personality", "pronoun") not in {(i["field"], i["kind"]) for i in issues}
    assert any(w["kind"] == "pronoun" for w in warnings)


def test_evidence_refs_must_exist_be_allowed_non_neutral_and_non_trait(packet):
    def with_ref(key, ids):
        b = good_brief()
        b["evidence_refs"][key] = ids
        return kinds(b, packet)

    assert ("evidence_refs.do.1", "ref_wrong") in with_ref("do.1", ["NOPE"])
    assert ("evidence_refs.do.1", "ref_neutral") in with_ref("do.1", ["TR.E", "MA"])               # TR.E is middling for this member
    assert ("evidence_refs.follow_up", "ref_not_allowed") in with_ref("follow_up", ["TR.A", "DF"])  # TR may not support follow_up
    assert ("evidence_refs.do.2", "ref_wrong") in with_ref("do.2", ["TR.A"])                       # only a trait cited
    assert ("evidence_refs.decides", "ref_not_allowed") in with_ref("decides", ["TR.N", "MA"])     # TR.N: personality or archetype only
    assert ("evidence_refs.speaks", "ref_wrong") in with_ref("speaks", ["BH.coop"])                # this member has no cross-party measure
    assert ("evidence_refs.dont.1", "ref_wrong") in with_ref("dont.1", ["AU.pace"])                # audio needs an IX/BH/DF id beside it
    assert with_ref("dont.1", ["AU.pace", "IX.discmark"]) == set()
    assert ("evidence_refs.do.3", "schema") not in with_ref("do.3", ["PROC"])


def test_proc_budget_for_a_rich_packet(packet):
    b = good_brief()
    for key in ("do.1", "do.2", "dont.1"):
        b["evidence_refs"][key] = ["PROC"]
    assert ("evidence_refs", "generic_advice") in kinds(b, packet)


def test_copying_five_consecutive_words_from_the_packet_fails_but_proper_nouns_are_exempt():
    p = copy.deepcopy(build()[0]["Alex Rich"])
    p["public_sensibilities"] = [{"id": "PS.1", "claim": "He grew up near the Red Rock flood zone", "may_support": ["personality"]}]
    b = edit(personality=good_brief()["personality"] + " He has told stories about the Red Rock flood zone.")
    assert ("personality", "copied_text") not in kinds(b, p)                       # carries a proper-noun phrase
    p["public_sensibilities"][0]["claim"] = "He grew up near the old river in a small town after a flood"
    five = edit(personality=good_brief()["personality"] + " He grew up near the old river.")        # five words in a row
    four = edit(personality=good_brief()["personality"] + " He grew up near a creek.")              # four in a row
    assert ("personality", "copied_text") in kinds(five, p) and ("personality", "copied_text") not in kinds(four, p)


def test_the_copy_rule_is_five_words_in_code_docstring_and_style_guide():
    guide = (ROOT / "fec/members/member_briefs_v3_style_guide.md").read_text(encoding="utf-8")
    twelve = guide.split("## 12. Anti-repetition", 1)[1].split("## 13.", 1)[0]
    fourteen = guide.split("## 14.", 1)[1].split("## 15.", 1)[0]
    assert re.search(r"five or more consecutive words", twelve) and "beyond four" not in twelve
    assert re.search(r"five or more consecutive words shared with any packet text", fourteen, re.I) and "Six-word overlap with the packet" not in fourteen
    assert "five" in V.copied_runs.__doc__.casefold() and V.COPY_RUN == 5


def test_archetype_rules_disqualified_barred_and_unsupported_descriptor(packet):
    p = copy.deepcopy(packet)
    for c in p["archetype_candidates"]:
        if c["animal"] == "Bull":
            c["disqualified"], c["contradicted"] = True, ["slot 3 strongly opposite"]
    assert ("archetype.animal", "archetype_misfit") in kinds(good_brief(), p)
    b = good_brief()
    b["archetype"]["animal"] = "Snake"
    assert ("archetype.animal", "archetype_misfit") in kinds(b, packet)
    b = good_brief()
    b["archetype"]["animal"], b["archetype"]["descriptor"] = "Beaver", "cooperative and wide-ranging"
    assert ("archetype.animal", "archetype_misfit") in kinds(b, packet)            # no coin recommendation, no stated reason
    b["archetype"]["justification"] = "His work is steady and broad, and no template animal is built on coalition building today."
    assert ("archetype.animal", "archetype_misfit") in kinds(b, packet)            # the packet says Bull: the stated reason no longer opens the door
    coin = copy.deepcopy(packet)
    coin["coin_recommended"].update(value=True, template_coin=True)
    assert ("archetype.animal", "archetype_misfit") not in kinds(b, coin)           # a coin recommendation allows a new name
    b = good_brief()
    b["archetype"]["descriptor"] = "skeptical and visionary"
    assert ("archetype.descriptor", "descriptor_unsupported") in kinds(b, packet)  # TR.O is middling here
    b["archetype"]["descriptor"] = "Skeptical, disciplined"
    assert ("archetype.descriptor", "form") in kinds(b, packet)


def test_reading_level_and_sentence_length(packet):
    long_sentence = " ".join(["word"] * 30) + "."
    assert ("speaks", "length") in kinds(edit(speaks=good_brief()["speaks"] + " " + long_sentence), packet)
    assert V.fk_grade(["The cat sat on the mat. The dog ran to the park."]) < 3
    assert V.fk_grade(["Notwithstanding considerable organizational complexity, institutional representatives demonstrate extraordinary methodological sophistication."]) > 15


def test_cross_member_duplication_is_flagged_above_the_share_threshold():
    packets, _ = build()
    briefs = {}
    for i in range(12):
        key = "M%d" % i
        briefs[key] = copy.deepcopy(good_brief())
        briefs[key]["personality"] = "Name%d is skeptical by default. " % i + " ".join("w%d_%d" % (i, j) for j in range(70))
        packets[key] = {**packets["Alex Rich"], "member": {**packets["Alex Rich"]["member"], "surname": "Name%d" % i, "display_name": "M%d" % i}}
    out = V.corpus_issues(briefs, packets)
    assert len(out) == 12 and all(i["kind"] == "repetition" for v in out.values() for i in v)
    briefs["M0"]["bottom_line"] += " Plus one unique closing line for member zero."
    assert V.corpus_issues({"M0": briefs["M0"]}, packets) == {}


def test_validate_all_reports_per_member_issue_lists(packet):
    packets = {"Alex Rich": copy.deepcopy(packet)}
    bad = edit(follow_up="Send it.")
    report = V.validate_all({"Alex Rich": good_brief(), "Ghost": bad}, packets)
    assert report["counts"] == {"briefs": 2, "passed": 1, "failed": 1} and report["failed"] == ["Ghost"]
    assert report["members"]["Ghost"]["issues"][0]["detail"] == "no packet for this member"
    unready = {"Alex Rich": {**copy.deepcopy(packet), "ready_for_writing": False, "not_ready_reasons": ["departed 2026-07-11"]}}
    assert V.validate_all({"Alex Rich": good_brief()}, unready)["failed"] == ["Alex Rich"]
    assert V.validate_all({"Alex Rich": good_brief()}, unready, require_ready=False)["failed"] == []


@pytest.fixture(scope="module")
def gold():
    """The six gold briefs and packets built for them from the real (gitignored) ledger; skips when absent."""
    exemplars = ROOT / "fec/members/member_briefs_v3_exemplars.json"
    needs = [exemplars, P.LEDGER_PATH, P.LEGISLATORS_PATH, P.CONGESTION_PATH, P.DESK_SRC_PATH, P.WIKI_PATH]
    if not all(p.is_file() for p in needs):
        pytest.skip("gold exemplars or the real ledger are not present (gitignored)")
    briefs = V.load_briefs(exemplars)
    inputs = P.load_content_inputs(ROOT)
    inputs["ledger_doc"]["members"] = {k: v for k, v in inputs["ledger_doc"]["members"].items() if k in briefs}
    seed = P.registry_seed(P.load_json(exemplars))
    packets, problems = P.build_all(basis_counts=None, levels_doc={"status": "FINAL", "members": {}}, coined=seed, **inputs)
    assert problems == []
    return briefs, packets


def test_exemplars_pass_against_packets_built_from_the_real_ledger(gold):
    briefs, packets = gold
    report = V.validate_all(briefs, packets, require_ready=False)
    assert report["failed"] == [], {n: report["members"][n]["issues"] for n in report["failed"]}


def test_fitzpatricks_coin_resolves_to_beaver_through_the_seed_registry(gold):
    _, packets = gold
    assert packets["Brian Fitzpatrick"]["coin_recommended"]["registry_match"]["animal"] == "Beaver"
    assert all(p["coin_recommended"]["registry_match"] is None for n, p in packets.items() if n != "Brian Fitzpatrick")


def test_the_six_gold_archetype_calls_are_unchanged(gold):
    _, packets = gold
    got = {n: ("COIN" if p["coin_recommended"]["value"] else p["suggested_animal"]) for n, p in packets.items()}
    assert got == {"Hakeem Jeffries": "Bull", "Mike Johnson": "Terrier", "Elizabeth Warren": "Blue Jay",
                   "Brian Fitzpatrick": "COIN", "Maxine Waters": "Bear", "Frank Lucas": "Tortoise"}


def tortoise_like(**overrides):
    """Everything that makes a Tortoise except reserve (TR.E), unless overridden."""
    args = dict(traits={"Conscientiousness": "well above most"}, behavior={"legislative_diligence": "above most", "conversational_dominance": "below most"},
                measures={"wpt_mean": "below most"}, turns=900, audio=audio(pace="below most"), hearing="below most", visible="below most",
                frame=MIXED[0], supported=MIXED[1])
    args.update(overrides)
    return args


def tortoise_of(rec):
    led = ledger()
    led["members"] = {"Alex Rich": {**rec, "name": "Alex Rich", "chamber": "House"}}
    packets, _ = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS, levels_doc=levels())
    packet = packets["Alex Rich"]
    return packet, {c["animal"]: c for c in packet["archetype_candidates"]}["Tortoise"]


def test_tortoise_requires_a_reserved_member():
    packet, t = tortoise_of(record(**tortoise_like(traits={"Conscientiousness": "well above most", "Extraversion": "below most"})))
    assert t["matched"] == 5 and not t["disqualified"] and packet["suggested_animal"] == "Tortoise"
    for band in ("middling", "above most", "among the highest in the chamber"):       # not reserved: ordinary, or outgoing
        packet, t = tortoise_of(record(**tortoise_like(traits={"Conscientiousness": "well above most", "Extraversion": band})))
        assert t["matched"] == 4 and t["disqualified"] and packet["suggested_animal"] != "Tortoise", band
    middling, t = tortoise_of(record(**tortoise_like(traits={"Conscientiousness": "well above most", "Extraversion": "middling"})))
    assert t["contradicted"] == ["required slot 1 unmatched"]                          # four other slots match; it still cannot be Tortoise
    _, outgoing = tortoise_of(record(**tortoise_like(traits={"Conscientiousness": "well above most", "Extraversion": "above most"})))
    assert "required slot 1 unmatched" in outgoing["contradicted"] and "TR.E hi" in outgoing["contradicted"]


def test_outgoing_or_floor_holding_members_are_disqualified_even_with_a_reserved_read():
    _, outgoing = tortoise_of(record(**tortoise_like(traits={"Extraversion": "below most"}, facets={"gregariousness": "among the highest in the chamber"},
                                                      behavior={"conversational_dominance": "among the highest in the chamber"})))
    assert outgoing["disqualified"] and "BH.dominance HI" in outgoing["contradicted"]


def test_low_visibility_and_mild_hearing_tone_share_one_slot():
    _, t = tortoise_of(record(traits={"Extraversion": "below most"}, hearing="below most", visible="below most", turns=0))
    assert t["matched_slots"] == [1, 5] and t["matched"] == 2                      # was three slots before the merge


def test_ties_still_break_on_matched_slots_then_alphabetically():
    live = [{"animal": a, "matched": 4, "slots": [], "matched_slots": [], "contradicted": [], "disqualified": False} for a in ("Owl", "Bear", "Bull")]
    view = P._View([], {"DF": False}, [], [], None)
    assert P.choose_animal(live, view, "standard")["animal"] == "Bear"


# ----------------------------------------------------------------------- publication gate ----
def lay_out_root(root, only=None):
    """The packet content inputs under a root, where P.input_paths() looks for them."""
    paths = P.input_paths(root)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    led = ledger()
    if only:
        led["members"] = {n: led["members"][n] for n in only}
    P.dump_atomic(led, paths["ledger"])
    P.dump_atomic(DESK, paths["desk_src"])
    P.dump_atomic({"schema": "member-wikipedia-biographies-v1", "members": {}}, paths["wikipedia"])
    P.dump_atomic(CONG, paths["congestion"])
    with paths["legislators"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["bioguide_id", "gender", "party", "last_name"])
        writer.writeheader()
        writer.writerows(LEGS)
    return paths


def publish(root):
    """A complete, consistent v3 publication under root: inputs, packets, stamped briefs, PASS receipt."""
    lay_out_root(root)
    inputs = P.load_content_inputs(root)
    packets, _ = P.build_all(basis_counts=COUNTS, levels_doc=levels(), **inputs)
    d = root / "data/members"
    P.dump_atomic({"schema": P.SCHEMA, "members": packets}, d / "member_brief_packets_v3.json")
    P.dump_atomic({"schema": P.RECEIPT_SCHEMA, "status": "READY", "allow_partial_longform": False,
                   "packets_sha256": P.sha256_file(d / "member_brief_packets_v3.json")}, d / "member_brief_packets_v3.receipt.json")
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": {n: {**good_brief(), "packet_sha256": packets[n]["packet_sha256"]} for n in packets}},
                  d / "member_briefs_v3.json")
    P.dump_atomic({"schema": V.RECEIPT_SCHEMA, "status": "PASS", "briefs_sha256": P.sha256_file(d / "member_briefs_v3.json")},
                  d / "member_briefs_v3.receipt.json")
    return root


def edit_json(path, change, mtime=None):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    change(doc)
    P.dump_atomic(doc, path)
    if mtime:
        os.utime(path, (mtime, mtime))


def queue_of(root):
    return json.loads((root / "data/members/member_briefs_v3_rewrite_queue.json").read_text(encoding="utf-8"))


def test_a_consistent_publication_shows_every_brief_clean_and_queues_nobody(tmp_path):
    root = publish(tmp_path)
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"} and summary["stale"] == []
    assert all("stale" not in p for p in payloads.values())
    assert queue_of(root)["members"] == {} and queue_of(root)["schema"] == V.QUEUE_SCHEMA
    assert payloads["Alex Rich"]["basis"]["line"].startswith("Basis: 400 floor speeches") and payloads["Alex Rich"]["pronoun"] == "he"
    assert payloads["Alex Rich"]["animal"] == "Bull" and "evidence_refs" not in payloads["Alex Rich"]


def test_when_one_members_packet_changes_only_that_member_is_flagged_and_the_rest_stay_clean(tmp_path):
    root = publish(tmp_path)
    edit_json(P.input_paths(root)["ledger"], lambda d: d["members"]["Beth Thin"]["traits"]["Agreeableness"].update(band=B("well above most")))
    errors, payloads, summary = V.load_publication(root, today="2026-10-21")
    assert errors == [] and summary["stale"] == ["Beth Thin"]
    assert set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"}                 # never hidden, never all blocked
    assert payloads["Beth Thin"]["stale"] == {"date": "2026-10-21"}
    assert "stale" not in payloads["Alex Rich"] and "stale" not in payloads["Cam Floor"]
    queue = queue_of(root)["members"]
    assert list(queue) == ["Beth Thin"] and queue["Beth Thin"]["brief_packet_sha256"] != queue["Beth Thin"]["current_packet_sha256"]
    assert queue["Beth Thin"]["first_diverged"] == "2026-10-21" and queue["Beth Thin"]["reason"] == "evidence changed since the brief was written"
    assert summary["reasons"] == {"Beth Thin": "evidence changed since the brief was written"}
    edit_json(P.input_paths(root)["ledger"], lambda d: d["members"]["Beth Thin"]["traits"]["Agreeableness"].update(band=B("middling")))
    assert V.load_publication(root)[2]["stale"] == [] and queue_of(root)["members"] == {}          # the queue follows the state


def test_changes_outside_a_members_packet_flag_nobody(tmp_path):
    root = publish(tmp_path)

    def change(doc):
        doc["sources_sha256"] = {"desk": "f" * 64, "basis": "e" * 64}                # the circular desk bundle hash moves
        doc["members"]["Cam Floor"]["effectiveness"]["do_pct"] = .01                 # read by the desk, not by the packet
        doc["members"]["Alex Rich"]["speech_profile"]["general_word_habits"]["raw_text_observations"]["interview_words"] = 5

    edit_json(P.input_paths(root)["ledger"], change)
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == [] and len(payloads) == 3


def test_a_member_added_to_the_ledger_flags_nobody_already_briefed(tmp_path):
    root = publish(tmp_path)
    paths = P.input_paths(root)
    edit_json(paths["ledger"], lambda d: d["members"].update({"Dee New": {**copy.deepcopy(d["members"]["Alex Rich"]), "name": "Dee New"}}))
    edit_json(paths["congestion"], lambda d: d.append({"member": "Dee New", "bioguide": "D1", "chamber": "House"}))
    with paths["legislators"].open("a", encoding="utf-8", newline="") as handle:
        handle.write("D1,F,Democrat,New\n")
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == [] and "Dee New" not in payloads


def test_a_member_whose_packet_cannot_be_rebuilt_is_flagged_not_hidden(tmp_path):
    root = publish(tmp_path)
    paths = P.input_paths(root)
    text = paths["legislators"].read_text(encoding="utf-8").replace("C1,,Independent,Floor\n", "")
    paths["legislators"].write_text(text, encoding="utf-8")
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == ["Cam Floor"] and "stale" in payloads["Cam Floor"] and "stale" not in payloads["Alex Rich"]
    assert queue_of(root)["members"]["Cam Floor"]["current_packet_sha256"] is None


def test_a_brief_that_stores_no_packet_hash_is_flagged_not_hidden(tmp_path):
    root = publish(tmp_path)
    d = root / "data/members"
    edit_json(d / "member_briefs_v3.json", lambda doc: doc["briefs"]["Beth Thin"].pop("packet_sha256"))
    edit_json(d / "member_briefs_v3.receipt.json", lambda doc: doc.update(briefs_sha256=P.sha256_file(d / "member_briefs_v3.json")))
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == ["Beth Thin"] and set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"}


def test_a_departed_member_shows_no_brief_and_is_never_queued(tmp_path, monkeypatch):
    root = publish(tmp_path)
    departed_roster(monkeypatch, ("Beth Thin", "2026-07-11"))
    edit_json(P.input_paths(root)["ledger"], lambda d: d["members"]["Beth Thin"]["traits"]["Agreeableness"].update(band=B("well above most")))
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and set(payloads) == {"Alex Rich", "Cam Floor"} and summary["departed"] == ["Beth Thin"]
    assert summary["stale"] == [] and queue_of(root)["members"] == {}
    assert V.departed_notice("Beth Thin") == "No longer serving (departed 2026-07-11)" and V.departed_notice("Alex Rich") is None


def test_the_shipped_departed_notice_for_graham_reads_as_the_ruling_says():
    assert V.departed_notice("Lindsey Graham") == "No longer serving (departed 2026-07-11)"


def test_a_packet_flagged_departed_is_never_shown_even_if_the_constant_forgot_it(tmp_path):
    root = publish(tmp_path)
    edit_json(root / "data/members/member_brief_packets_v3.json", lambda d: d["members"]["Beth Thin"].update(departed=True))
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and "Beth Thin" not in payloads and summary["departed"] == ["Beth Thin"]


@pytest.mark.parametrize("what,message", [
    ("receipt_missing", "missing member_briefs_v3.receipt.json"), ("packets_missing", "missing member_brief_packets_v3.json"),
    ("receipt_not_pass", "brief receipt is not a PASS receipt"), ("briefs_edited", "briefs file changed after its receipt was written"),
    ("schema", "brief or packet schema mismatch"), ("inputs_missing", "cannot recompute the packets")])
def test_file_level_problems_show_nothing_and_say_why(tmp_path, what, message):
    root = publish(tmp_path)
    d = root / "data/members"
    if what == "receipt_missing":
        (d / "member_briefs_v3.receipt.json").unlink()
    elif what == "packets_missing":
        (d / "member_brief_packets_v3.json").unlink()
    elif what == "receipt_not_pass":
        edit_json(d / "member_briefs_v3.receipt.json", lambda doc: doc.update(status="FAIL"))
    elif what == "briefs_edited":
        (d / "member_briefs_v3.json").write_text((d / "member_briefs_v3.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif what == "schema":
        edit_json(d / "member_brief_packets_v3.json", lambda doc: doc.update(schema="other"))
    else:
        P.input_paths(root)["ledger"].unlink()
    errors, payloads, summary = V.load_publication(root)
    assert errors and message in errors[0] and payloads == {} and summary == {}


def test_packet_receipt_and_ledger_hashes_are_not_part_of_the_desk_gate(tmp_path):
    """The old gate bound the whole ledger file and every ledger input; the per-member hash replaced it."""
    root = publish(tmp_path)
    edit_json(P.input_paths(root)["ledger"], lambda d: d.update(note="rebuilt"))        # a different ledger file, same member content
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == [] and len(payloads) == 3


def test_a_brief_must_name_the_packet_it_was_written_against(packet):
    b = good_brief()
    b["packet_sha256"] = packet["packet_sha256"]
    assert V.validate_brief(b, packet, require_binding=True)[0] == []
    assert V.validate_brief(good_brief(), packet)[0] == []                          # not required for dry runs and exemplars
    issues = V.validate_brief(good_brief(), packet, require_binding=True)[0]
    assert [(i["field"], i["kind"]) for i in issues] == [("packet_sha256", "schema")]
    b["packet_sha256"] = "0" * 64
    assert [(i["field"], i["kind"]) for i in V.validate_brief(b, packet)[0]] == [("packet_sha256", "schema")]


def test_validator_cli_exits_nonzero_on_any_fail_and_writes_a_report(tmp_path):
    packets, _ = build()
    P.dump_atomic({"schema": P.SCHEMA, "members": packets}, tmp_path / "p.json")
    bad = good_brief()
    bad["follow_up"] = "Send it."
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": {"Alex Rich": good_brief(), "Beth Thin": bad}}, tmp_path / "b.json")
    rc = V.main(["--briefs", str(tmp_path / "b.json"), "--packets", str(tmp_path / "p.json"), "--report", str(tmp_path / "r.json")])
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert rc == 1 and report["failed"] == ["Beth Thin"] and report["members"]["Alex Rich"]["pass"] is True
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": {"Alex Rich": good_brief()}}, tmp_path / "ok.json")
    assert V.main(["--briefs", str(tmp_path / "ok.json"), "--packets", str(tmp_path / "p.json")]) == 0
    assert V.main(["--briefs", str(tmp_path / "missing.json"), "--packets", str(tmp_path / "p.json")]) == 2


def test_receipt_is_refused_when_the_roster_is_incomplete_or_the_packets_are_partial(tmp_path):
    packets, _ = build()
    P.dump_atomic({"schema": P.SCHEMA, "members": packets}, tmp_path / "p.json")
    P.dump_atomic({"schema": P.RECEIPT_SCHEMA, "status": "NOT_READY_PARTIAL_LONGFORM"}, tmp_path / "pr.json")
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": {"Alex Rich": good_brief()}}, tmp_path / "b.json")
    (tmp_path / "l.json").write_text(json.dumps(ledger()), encoding="utf-8")
    args = ["--briefs", str(tmp_path / "b.json"), "--packets", str(tmp_path / "p.json"), "--packet-receipt", str(tmp_path / "pr.json"),
            "--ledger", str(tmp_path / "l.json"), "--receipt", str(tmp_path / "out.json"), "--write-receipt"]
    assert V.main(args) == 1 and not (tmp_path / "out.json").exists()


# ----------------------------------------------------------------------------- regen guard ----
def test_regeneration_skips_cleanly_when_v3_is_published_and_touches_nothing(tmp_path, capsys):
    """The chain runs this step with --activate: a skip must exit 0 (not fail the chain) and write nothing."""
    briefs, receipt = tmp_path / "member_briefs_v3.json", tmp_path / "member_briefs_v3.receipt.json"
    briefs.write_text("{}", encoding="utf-8")
    receipt.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    rc = RMP.main(["--v3-briefs", str(briefs), "--v3-receipt", str(receipt), "--legacy", str(tmp_path / "legacy.json"),
                   "--candidate-out", str(tmp_path / "cand.json"), "--artifact", str(tmp_path / "member_narratives_v2.json"),
                   "--receipt", str(tmp_path / "semantic.json"), "--activate"])
    assert rc == 0 and "SKIPPED: v3 member briefs" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == [briefs.name, receipt.name]


def test_regeneration_guard_does_not_trip_without_a_pass_receipt(tmp_path):
    briefs, receipt = tmp_path / "member_briefs_v3.json", tmp_path / "member_briefs_v3.receipt.json"
    assert RMP.v3_publication_present(briefs, receipt) is False
    briefs.write_text("{}", encoding="utf-8")
    assert RMP.v3_publication_present(briefs, receipt) is False             # briefs alone are not a publication
    receipt.write_text(json.dumps({"status": "PARTIAL"}), encoding="utf-8")
    assert RMP.v3_publication_present(briefs, receipt) is False
    receipt.write_text("not json", encoding="utf-8")
    assert RMP.v3_publication_present(briefs, receipt) is True              # unreadable receipt: stay safe
    with pytest.raises((FileNotFoundError, SystemExit)) as run:             # a legacy run still proceeds to its own inputs
        receipt.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
        RMP.main(["--v3-briefs", str(briefs), "--v3-receipt", str(receipt), "--legacy", str(tmp_path / "legacy.json")])
    assert "SKIPPED" not in str(run.value)


def test_backup_before_write_copies_the_live_file_and_never_overwrites_a_backup(tmp_path):
    live = tmp_path / "receipt.json"
    live.write_text("v1", encoding="utf-8")
    first = RMP.backup_before_write(live, tmp_path / "bk", "20261020T000000Z")
    live.write_text("v2", encoding="utf-8")
    second = RMP.backup_before_write(live, tmp_path / "bk", "20261020T000001Z")
    assert first.read_text(encoding="utf-8") == "v1" and second.read_text(encoding="utf-8") == "v2" and first != second
    assert RMP.backup_before_write(tmp_path / "absent.json", tmp_path / "bk", "x") is None


# ------------------------------------------------------------------ end to end (tmp inputs) ----
def lay_out_inputs(tmp_path, *, status="FINAL", extra_levels=(), diagnostics=True):
    """A one-member world: content inputs where input_paths() expects them, plus counts and levels files."""
    root = tmp_path / "in"
    paths = lay_out_root(root, only=["Alex Rich"])
    c = COUNTS["Alex Rich"]
    paths["counts"] = root / "counts.csv"
    paths["counts"].write_text(
        "member,crec_count,crec_min_date,crec_max_date,chrg_count,chrg_min_date,chrg_max_date\n"
        "Alex Rich,%d,%s,%s,%d,%s,%s\n" % (c["floor_speeches"], c["floor_from"], c["floor_to"], c["hearings"], c["hearing_from"], c["hearing_to"]),
        encoding="utf-8")
    lv = levels(status=status)
    lv["members"] = {"Alex Rich": lv["members"]["Alex Rich"], **{n: level(n, None, reason="New member: discovery pending") for n in extra_levels}}
    paths["levels"] = root / "levels.json"
    paths["levels"].write_text(json.dumps(lv), encoding="utf-8")
    paths["diagnostics"] = P.default_diagnostics_path(paths["levels"])
    if diagnostics:
        paths["diagnostics"].write_text(json.dumps(diagnostics if isinstance(diagnostics, dict) else {}), encoding="utf-8")
    return paths


def run_builder(tmp_path, paths, *extra):
    argv = ["--ledger", paths["ledger"], "--desk-src", paths["desk_src"], "--wikipedia", paths["wikipedia"], "--legislators", paths["legislators"],
            "--congestion", paths["congestion"], "--model-bio", tmp_path / "none.json", "--basis-counts", paths["counts"],
            "--longform-levels", paths["levels"], "--briefs", tmp_path / "none-briefs.json", "--exemplars", tmp_path / "none-ex.json",
            "--out", tmp_path / "out/packets.json", "--receipt", tmp_path / "out/packets.receipt.json", *extra]
    return P.main([str(a) for a in argv])


def test_builder_end_to_end_final_levels_marks_packets_ready_and_hashes_every_input(tmp_path):
    paths = lay_out_inputs(tmp_path)
    assert run_builder(tmp_path, paths) == 0
    receipt = json.loads((tmp_path / "out/packets.receipt.json").read_text(encoding="utf-8"))
    doc = json.loads((tmp_path / "out/packets.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "READY" and receipt["longform_levels_status"] == "FINAL" and receipt["allow_partial_longform"] is False
    assert receipt["packets_sha256"] == P.sha256_file(tmp_path / "out/packets.json")
    assert {"ledger", "desk_src", "wikipedia", "legislators", "congestion", "basis_counts", "longform_levels", "style_guide"} <= set(receipt["inputs"])
    assert all(len(v["sha256"]) == 64 for v in receipt["inputs"].values())
    assert receipt["counts"]["ready_for_writing"] == 1 and receipt["counts"]["deferred_new_members"] == []
    assert receipt["counts"]["levels_extra_keys"] == [] and set(doc["members"]) == {"Alex Rich"}
    assert doc["members"]["Alex Rich"]["ready_for_writing"] is True and "confidence_rule" in receipt
    assert len(doc["members"]["Alex Rich"]["packet_sha256"]) == 64 and "ledger_source_drift" not in receipt
    assert "do_pct" in receipt["effectiveness_definitions"] and "party_unity" in receipt


def test_builder_end_to_end_partial_levels_status_is_recorded_and_never_ready_without_the_dry_run_flag(tmp_path):
    paths = lay_out_inputs(tmp_path, status="PARTIAL")
    assert run_builder(tmp_path, paths) == 0
    receipt = json.loads((tmp_path / "out/packets.receipt.json").read_text(encoding="utf-8"))
    doc = json.loads((tmp_path / "out/packets.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "NOT_READY_PARTIAL_LONGFORM" and receipt["longform_levels_status"] == "PARTIAL"
    assert doc["members"]["Alex Rich"]["ready_for_writing"] is False and receipt["counts"]["ready_for_writing"] == 0
    assert run_builder(tmp_path, paths, "--allow-partial-longform") == 0
    flagged = json.loads((tmp_path / "out/packets.receipt.json").read_text(encoding="utf-8"))
    assert flagged["status"] == "DRY_RUN_PARTIAL_ALLOWED" and flagged["allow_partial_longform"] is True


def test_builder_end_to_end_fails_closed_on_unknown_levels_member_and_missing_files(tmp_path, capsys):
    assert run_builder(tmp_path / "b", lay_out_inputs(tmp_path / "b", extra_levels=["Stranger Danger"])) == 2
    assert "the ledger lacks" in capsys.readouterr().err
    paths = lay_out_inputs(tmp_path / "c")
    paths["levels"].unlink()
    assert run_builder(tmp_path / "c", paths) == 2 and "longform-levels" in capsys.readouterr().err


def test_final_levels_without_the_diagnostics_file_fail_closed_but_partial_levels_may_proceed(tmp_path, capsys):
    paths = lay_out_inputs(tmp_path / "a", diagnostics=False)
    assert paths["diagnostics"] == paths["levels"].with_name("levels.diagnostics.json") and not paths["diagnostics"].exists()
    assert run_builder(tmp_path / "a", paths) == 2
    err = capsys.readouterr().err
    assert "FAIL CLOSED" in err and "diagnostics" in err and not (tmp_path / "a/out").exists()
    partial = lay_out_inputs(tmp_path / "b", status="PARTIAL", diagnostics=False)
    assert run_builder(tmp_path / "b", partial, "--allow-partial-longform") == 0 and "diagnostics" in capsys.readouterr().err
    receipt = json.loads((tmp_path / "b/out/packets.receipt.json").read_text(encoding="utf-8"))
    assert "longform_diagnostics" not in receipt["inputs"]
    assert run_builder(tmp_path / "b", partial, "--allow-partial-longform", "--longform-diagnostics", tmp_path / "typo.json") == 2     # a named file must exist


def test_the_diagnostics_file_is_read_hashed_in_the_receipt_and_puts_its_era_on_the_item(tmp_path):
    turn = {"turn_id": "t-01", "source_id": "src-1", "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP", "semantic_review_qualified": True, "channel": "Podcast",
            "recorded_at": "2022-03-24", "semantic_findings": [{"finding": "f", "meeting_implication": "m", "scope": "s"}]}
    paths = lay_out_inputs(tmp_path, diagnostics={"Alex Rich": {"rec-1": {"source_id": "src-1", "source_era": "PRE_CONGRESS_LOCAL_OFFICE_X", "recorded_at": "2021-02-12"}}})
    edit_json(paths["ledger"], lambda d: d["members"]["Alex Rich"]["speech_profile"].update(longform_interview_evidence={"full_turns": [turn]}))
    assert run_builder(tmp_path, paths) == 0
    receipt = json.loads((tmp_path / "out/packets.receipt.json").read_text(encoding="utf-8"))
    doc = json.loads((tmp_path / "out/packets.json").read_text(encoding="utf-8"))
    assert receipt["inputs"]["longform_diagnostics"]["sha256"] == P.sha256_file(paths["diagnostics"])
    items = {i["id"]: i for i in doc["members"]["Alex Rich"]["longform"]}
    assert items["LF.t-01.1"]["era"] == "PRE_CONGRESS_LOCAL_OFFICE_X" and items["LF.t-01.1"]["may_support"] == ["personality", "speaks"]
    assert receipt["counts"]["longform_turns_by_era"] == {"PRE_CONGRESS_LOCAL_OFFICE_X": 1} and receipt["counts"]["longform_turns_without_diagnostics_era"] == 0


def test_dry_run_tolerates_missing_counts_and_writes_nothing(tmp_path, capsys):
    paths = lay_out_inputs(tmp_path)
    paths["counts"].unlink()
    assert run_builder(tmp_path, paths, "--dry-run") == 0
    out = capsys.readouterr()
    assert "archetype distribution" in out.out and "WARNING (dry run)" in out.err and not (tmp_path / "out").exists()


def test_builder_validator_desk_chain_with_one_member(tmp_path):
    paths = lay_out_inputs(tmp_path)
    assert run_builder(tmp_path, paths) == 0
    out, root = tmp_path / "out", tmp_path / "in"
    packets = json.loads((out / "packets.json").read_text(encoding="utf-8"))["members"]
    stamped = {"Alex Rich": {**good_brief(), "packet_sha256": packets["Alex Rich"]["packet_sha256"]}}
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": stamped}, out / "briefs.json")
    common = ["--briefs", out / "briefs.json", "--packets", out / "packets.json", "--packet-receipt", out / "packets.receipt.json",
              "--ledger", paths["ledger"], "--receipt", out / "briefs.receipt.json", "--write-receipt"]
    assert V.main([str(a) for a in common]) == 0
    gate = dict(briefs_path=out / "briefs.json", receipt_path=out / "briefs.receipt.json", packets_path=out / "packets.json",
                packet_receipt_path=out / "packets.receipt.json", queue_path=out / "queue.json")
    errors, briefs, summary = V.load_publication(root, **gate)
    assert errors == [] and list(briefs) == ["Alex Rich"] and summary["stale"] == [] and briefs["Alex Rich"]["basis"]["line"].startswith("Basis:")
    edit_json(paths["ledger"], lambda d: d["members"]["Alex Rich"]["traits"]["Openness"].update(band=B("among the highest in the chamber")))
    errors, briefs, summary = V.load_publication(root, **gate)
    assert errors == [] and summary["stale"] == ["Alex Rich"] and "stale" in briefs["Alex Rich"]       # shown, flagged
    unstamped = {"Alex Rich": good_brief()}
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": unstamped}, out / "unstamped.json")
    (out / "briefs.receipt.json").unlink()
    refused = [str(a) for a in common]
    refused[1] = str(out / "unstamped.json")
    assert V.main(refused) == 1 and not (out / "briefs.receipt.json").exists()          # the receipt needs the packet hash



# =========================================================================== Opus review: fixes ====
# ---- 1. descriptor form
@pytest.mark.parametrize("animal", sorted(P.ARCHETYPES))
def test_every_default_descriptor_passes_the_form_check(packet, animal):
    b = good_brief()
    b["archetype"]["descriptor"] = P.ARCHETYPES[animal]["descriptor"]
    assert ("archetype.descriptor", "form") not in kinds(b, packet), P.ARCHETYPES[animal]["descriptor"]


def test_the_ten_template_animals_are_all_covered_by_the_descriptor_test():
    assert len(P.ARCHETYPES) == 10 and set(P.ARCHETYPES) == set(P.TEMPLATE_ANIMALS)


@pytest.mark.parametrize("bad", ["Skeptical and careful", "skeptical", "skeptical, careful", "skeptical and careful and warm",
                                 "skeptical, careful and warm", "skeptical, careful, warm, and firm", "skeptical & careful",
                                 "skeptical and very very very hard indeed", "skeptical and careful with all the people too"])
def test_descriptor_form_still_rejects_malformed_descriptors(packet, bad):
    b = good_brief()
    b["archetype"]["descriptor"] = bad
    assert ("archetype.descriptor", "form") in kinds(b, packet)


# ---- 2. the packet hash holds no other member's data
def test_packet_hash_ignores_percentiles_in_the_evidence_and_the_appeals(packet):
    q = copy.deepcopy(packet)
    for item in q["evidence"]:
        if item.get("pct") is not None:
            item["pct"] = round(item["pct"] / 2, 3)
    q["appeals"]["agency_percentile"], q["appeals"]["communion_percentile"] = .11, .22
    assert any(i.get("pct") is not None for i in packet["evidence"]) and P.content_sha256(q) == packet["packet_sha256"]


def test_shifting_another_members_percentile_flags_nobody(tmp_path):
    root = publish(tmp_path)

    def shift(doc):                                             # same band label, different percentile, on two members
        doc["members"]["Beth Thin"]["traits"]["Agreeableness"]["band"]["pct"] = 0.47
        doc["members"]["Alex Rich"]["behavior"]["legislative_diligence"]["pct"] = 0.97

    edit_json(P.input_paths(root)["ledger"], shift)
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and summary["stale"] == [] and all("stale" not in p for p in payloads.values())


def population(others, mine=1.0, n=39):
    """A 40-member world where the setting-contrast spread comes from the other members."""
    pair = {"first": "npr", "second": "sunday_show", "first_clips": 40, "second_clips": 30}
    led = {"schema": "member-narrative-evidence-v4", "status": "ACTIVE_FAIL_CLOSED_INPUT", "sources_sha256": {}, "members": {}}
    legs, cong = [], []
    for i in range(n + 1):
        name = "P%d" % i
        delta = mine if i == n else others(i)
        led["members"][name] = {**record(contrasts=[{**pair, "delta_first_minus_second": {"pace": delta}}]), "name": name, "chamber": "House"}
        legs.append({"bioguide_id": "g%d" % i, "gender": "M", "party": "Democrat", "last_name": "P%d" % i})
        cong.append({"member": name, "bioguide": "g%d" % i, "chamber": "House"})
    inputs = dict(legislators=legs, congestion=cong, model_bio={}, desk_src={"members": []}, wiki={})
    return P.build_contents(led, **inputs)[0]


def test_a_setting_contrast_verdict_that_flips_because_of_other_members_does_not_change_this_members_hash():
    tight = population(lambda i: (i - 19) / 40)                  # narrow spread: this member's delta of 1.0 stands out
    wide = population(lambda i: (i - 19) * 3)                    # wide spread: the same delta is ordinary
    mine = lambda c: next(x for x in c["P39"]["evidence"] if x["id"].startswith("SC."))
    assert mine(tight)["meaningful"] is True and mine(wide)["meaningful"] is False         # the derived verdict did flip
    assert tight["P39"]["packet_sha256"] == wide["P39"]["packet_sha256"]                    # the hash did not
    own = population(lambda i: (i - 19) / 40, mine=1.5)
    assert own["P39"]["packet_sha256"] != tight["P39"]["packet_sha256"]                      # this member's OWN delta still counts


# ---- 3. one member that cannot be rebuilt
def corrupt_label(doc, name="Beth Thin"):
    doc["members"][name]["traits"]["Extraversion"]["band"]["label"] = "bogus"


def test_build_contents_records_the_error_per_member_and_keeps_building():
    led = ledger()
    corrupt_label(led)
    contents, problems = P.build_contents(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={})
    assert set(contents) == {"Alex Rich", "Cam Floor"} and problems == ["Beth Thin: unknown band label 'bogus'"]
    assert P.problems_by_member(problems) == {"Beth Thin": "unknown band label 'bogus'"}


@pytest.mark.parametrize("damage,expect", [
    (lambda d: d["members"]["Beth Thin"].update(chamber=None), "chamber must be House or Senate"),
    (lambda d: d["members"]["Beth Thin"]["speech_profile"]["motivating_appeals"].update(assertive_vs_communicative_frame="Something new"),
     "unrecognized appeal frame"),
    (lambda d: d["members"]["Beth Thin"]["speech_profile"].update(longform_interview_evidence={"full_turns": [{"semantic_findings": [{}]}]}),
     "KeyError 'turn_id'")])
def test_each_kind_of_member_build_error_is_contained_to_that_member(damage, expect):
    led = ledger()
    damage(led)
    contents, problems = P.build_contents(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={})
    assert set(contents) == {"Alex Rich", "Cam Floor"} and len(problems) == 1 and problems[0].startswith("Beth Thin: ") and expect in problems[0]


def test_the_desk_flags_only_the_member_it_cannot_rebuild_and_records_why(tmp_path):
    root = publish(tmp_path)
    edit_json(P.input_paths(root)["ledger"], corrupt_label)
    errors, payloads, summary = V.load_publication(root, today="2026-10-21")
    assert errors == [] and summary["stale"] == ["Beth Thin"] and set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"}
    reason = "cannot rebuild the packet: unknown band label 'bogus'"
    assert summary["reasons"] == {"Beth Thin": reason}
    entry = queue_of(root)["members"]["Beth Thin"]
    assert entry["current_packet_sha256"] is None and entry["reason"] == reason and payloads["Beth Thin"]["stale"] == {"date": "2026-10-21"}
    assert "stale" not in payloads["Alex Rich"] and "stale" not in payloads["Cam Floor"]


def test_the_packet_builder_cli_fails_closed_on_a_member_it_cannot_build(tmp_path, capsys):
    paths = lay_out_inputs(tmp_path)
    edit_json(paths["ledger"], lambda d: corrupt_label(d, "Alex Rich"))
    assert run_builder(tmp_path, paths) == 2 and "Alex Rich: unknown band label 'bogus'" in capsys.readouterr().err
    assert not (tmp_path / "out/packets.json").exists()


# ---- 4. corrupt JSON never crashes the desk
@pytest.mark.parametrize("which", ["receipt", "briefs", "packets"])
@pytest.mark.parametrize("garbage", ["{not json", "[1, 2]", ""])
def test_corrupt_receipt_briefs_or_packets_give_a_file_level_error_not_a_crash(tmp_path, which, garbage):
    root = publish(tmp_path)
    d = root / "data/members"
    target = {"receipt": "member_briefs_v3.receipt.json", "briefs": "member_briefs_v3.json", "packets": "member_brief_packets_v3.json"}[which]
    (d / target).write_text(garbage, encoding="utf-8")
    if which == "briefs":                                      # keep the receipt hash valid so the load itself is what fails
        edit_json(d / "member_briefs_v3.receipt.json", lambda doc: doc.update(briefs_sha256=P.sha256_file(d / target)))
    errors, payloads, summary = V.load_publication(root)
    assert errors and payloads == {} and summary == {}
    assert "unreadable" in errors[0] or "not a PASS receipt" in errors[0] or "schema mismatch" in errors[0]


def test_a_non_utf8_file_and_a_corrupt_packet_receipt_do_not_crash_either(tmp_path):
    root = publish(tmp_path)
    d = root / "data/members"
    (d / "member_brief_packets_v3.receipt.json").write_bytes(b"\xff\xfe not utf8")
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and len(payloads) == 3 and all(p["basis"] is None for p in payloads.values())     # briefs stay, Basis is withheld
    (d / "member_briefs_v3.receipt.json").write_bytes(b"\xff\xfe not utf8")
    assert V.load_publication(root)[0] and "unreadable" in V.load_publication(root)[0][0]


# ---- 5. a Basis line only from a READY, hash-matched packet receipt
def test_partial_longform_packets_cannot_be_written_to_the_live_default_path(tmp_path, capsys, monkeypatch):
    paths = lay_out_inputs(tmp_path, status="PARTIAL")
    live = tmp_path / "live"
    monkeypatch.setattr(P, "PACKETS_PATH", live / "member_brief_packets_v3.json")
    monkeypatch.setattr(P, "RECEIPT_PATH", live / "member_brief_packets_v3.receipt.json")
    argv = ["--ledger", paths["ledger"], "--desk-src", paths["desk_src"], "--wikipedia", paths["wikipedia"], "--legislators", paths["legislators"],
            "--congestion", paths["congestion"], "--model-bio", tmp_path / "none.json", "--basis-counts", paths["counts"],
            "--longform-levels", paths["levels"], "--briefs", tmp_path / "none-b.json", "--exemplars", tmp_path / "none-e.json"]
    run = lambda *extra: P.main([str(a) for a in (*argv, *extra)])
    assert run("--allow-partial-longform") == 2 and "refusing" in capsys.readouterr().err and not live.exists()          # default --out
    assert run("--allow-partial-longform", "--out", live / "member_brief_packets_v3.json", "--receipt", tmp_path / "x.json") == 2   # live path, explicit
    assert not live.exists()
    assert run("--allow-partial-longform", "--out", tmp_path / "scratch/p.json", "--receipt", tmp_path / "scratch/r.json") == 0     # elsewhere: fine
    assert run("--allow-partial-longform", "--dry-run") == 0 and not live.exists()                                              # dry run: fine


@pytest.mark.parametrize("what,reason", [
    ("missing", "packet receipt is missing"), ("not_ready", "packet receipt is not READY (status NOT_READY_PARTIAL_LONGFORM)"),
    ("allow_partial", "packet receipt was written with --allow-partial-longform"),
    ("hash", "packets changed after the packet receipt was written"), ("schema", "packet receipt is not a packet receipt")])
def test_the_basis_line_is_withheld_visibly_unless_the_packet_receipt_is_ready_and_matches(tmp_path, what, reason):
    root = publish(tmp_path)
    d = root / "data/members"
    pr = d / "member_brief_packets_v3.receipt.json"
    if what == "missing":
        pr.unlink()
    elif what == "not_ready":
        edit_json(pr, lambda doc: doc.update(status="NOT_READY_PARTIAL_LONGFORM"))
    elif what == "allow_partial":
        edit_json(pr, lambda doc: doc.update(allow_partial_longform=True))
    elif what == "hash":
        edit_json(d / "member_brief_packets_v3.json", lambda doc: doc.update(note="edited"))
    else:
        edit_json(pr, lambda doc: doc.update(schema="other"))
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"}                 # the briefs still show
    assert all(p["basis"] is None and p["basis_withheld"] == reason for p in payloads.values()) and summary["basis_withheld"] == reason


def test_the_basis_line_shows_for_a_ready_hash_matched_receipt(tmp_path):
    root = publish(tmp_path)
    errors, payloads, summary = V.load_publication(root)
    assert summary["basis_withheld"] is None and all(p["basis"]["line"].startswith("Basis:") and "basis_withheld" not in p for p in payloads.values())


def test_a_member_with_no_packet_or_basis_keeps_the_brief_and_says_why_the_basis_is_missing(tmp_path):
    root = publish(tmp_path)
    d = root / "data/members"

    def damage(doc):
        doc["members"]["Cam Floor"]["basis"] = None
        del doc["members"]["Beth Thin"]

    edit_json(d / "member_brief_packets_v3.json", damage)
    edit_json(d / "member_brief_packets_v3.receipt.json", lambda doc: doc.update(packets_sha256=P.sha256_file(d / "member_brief_packets_v3.json")))
    errors, payloads, summary = V.load_publication(root)
    assert errors == [] and set(payloads) == {"Alex Rich", "Beth Thin", "Cam Floor"}
    assert payloads["Cam Floor"]["basis_withheld"] == "no basis was computed for this member"
    assert payloads["Beth Thin"]["basis_withheld"] == "no packet for this member" and payloads["Alex Rich"]["basis"]["line"].startswith("Basis:")


# ---- 9. the date a hash first diverged
def test_the_first_diverged_date_is_carried_between_builds_and_resets_on_recovery(tmp_path):
    root = publish(tmp_path)
    ledger_file = P.input_paths(root)["ledger"]
    flag = lambda band: edit_json(ledger_file, lambda d: d["members"]["Beth Thin"]["traits"]["Agreeableness"].update(band=B(band)))
    flag("well above most")
    assert V.load_publication(root, today="2026-10-21")[1]["Beth Thin"]["stale"] == {"date": "2026-10-21"}
    assert V.load_publication(root, today="2026-10-25")[1]["Beth Thin"]["stale"] == {"date": "2026-10-21"}     # carried
    flag("well below most")                                                                                       # moves again, still diverged
    assert V.load_publication(root, today="2026-10-27")[1]["Beth Thin"]["stale"] == {"date": "2026-10-21"}
    assert queue_of(root)["members"]["Beth Thin"]["first_diverged"] == "2026-10-21"
    flag("middling")                                                                                              # back to what the writer saw
    assert V.load_publication(root, today="2026-11-01")[2]["stale"] == [] and queue_of(root)["members"] == {}
    flag("well above most")
    assert V.load_publication(root, today="2026-11-05")[1]["Beth Thin"]["stale"] == {"date": "2026-11-05"}      # a new divergence


def test_a_rewritten_brief_that_diverges_again_gets_a_new_date_and_a_bad_queue_file_is_ignored(tmp_path):
    root = publish(tmp_path)
    d = root / "data/members"
    edit_json(P.input_paths(root)["ledger"], lambda doc: doc["members"]["Beth Thin"]["traits"]["Agreeableness"].update(band=B("well above most")))
    V.load_publication(root, today="2026-10-21")
    edit_json(d / "member_briefs_v3.json", lambda doc: doc["briefs"]["Beth Thin"].update(packet_sha256="1" * 64))        # a different brief
    edit_json(d / "member_briefs_v3.receipt.json", lambda doc: doc.update(briefs_sha256=P.sha256_file(d / "member_briefs_v3.json")))
    assert V.load_publication(root, today="2026-10-30")[1]["Beth Thin"]["stale"] == {"date": "2026-10-30"}
    (d / "member_briefs_v3_rewrite_queue.json").write_text("{broken", encoding="utf-8")
    assert V.load_publication(root, today="2026-11-02")[1]["Beth Thin"]["stale"] == {"date": "2026-11-02"}


# ---- 10. curly apostrophes
def test_curly_apostrophes_are_normalized_before_the_first_word_and_plural_verb_checks(packet):
    b = good_brief()
    b["dont"][0] = "Don\u2019t lean on pressure or repeat the ask to wear him down"
    assert ("dont.1", "form") in kinds(b, packet)
    b = good_brief()
    b["speaks"] = good_brief()["speaks"] + " It\u2019s worth noting that his pace is quick."
    assert ("speaks", "banned_phrase") in kinds(b, packet)
    they = copy.deepcopy(packet)
    they["member"]["pronoun"] = "they"
    b = good_brief()
    b["bottom_line"] = "Bring facts, because they doesn\u2019t move on pressure alone and need a clear case from you today."
    assert ("bottom_line", "pronoun") in kinds(b, they)
    assert V.all_text(edit(bottom_line="It\u2019s here"))["bottom_line"] == "It's here"


# ---- 15. new checks
@pytest.mark.parametrize("field,text", [
    ("personality", " He has voted on three hundred bills."), ("speaks", " He gave ten interviews last year."),
    ("decides", " Pressure works about twice as well on him."), ("personality", " He casts one hundred percent of his votes.")])
def test_spelled_out_numbers_describing_the_member_are_barred_in_the_profile(packet, field, text):
    assert (field, "numbers") in kinds(edit(**{field: good_brief()[field] + text}), packet)


def test_spelled_out_numbers_are_fine_in_reader_advice_and_one_of_the_most_is_fine_everywhere(packet):
    assert kinds(edit(follow_up="Email a brief note the same day, then a second note in two or three days, naming the source beside each claim."), packet) == set()
    b = good_brief()
    b["do"][1] = "Limit every request to a line or two"
    assert kinds(b, packet) == set()
    assert ("personality", "numbers") not in kinds(edit(personality=good_brief()["personality"] + " He is one of the most careful members."), packet)


def test_the_animal_is_named_only_inside_the_archetype_object(packet):
    for field, text in (("bottom_line", "Bring facts and treat him like a bull in a meeting, because a stronger case moves him more than pressure."),
                        ("speaks", good_brief()["speaks"] + " His manner is that of a Bull.")):
        assert (field, "archetype_misfit") in kinds(edit(**{field: text}), packet)
    b = good_brief()
    b["do"][0] = "Put the decision you need in your first sentence like a bull"
    assert ("do.1", "archetype_misfit") in kinds(b, packet)
    assert not any(f.startswith("archetype") and k == "archetype_misfit" for f, k in kinds(good_brief(), packet))
    bear = copy.deepcopy(packet)
    bear["archetype_candidates"] = [{"animal": "Bear", "matched": 5, "slots": [], "matched_slots": [], "contradicted": [], "disqualified": False}]
    b = edit(follow_up="Bear in mind that a short memo within a day answers each objection he raised, point by point, with sources.")
    b["archetype"] = {**good_brief()["archetype"], "animal": "Bear"}
    assert ("follow_up", "archetype_misfit") not in kinds(b, bear)                                       # the verb "bear" is not the animal
    b["follow_up"] = "Lead with the people, as a bear protects its own, and send a short memo within a day with a source for every fact."
    assert ("follow_up", "archetype_misfit") in kinds(b, bear)


@pytest.mark.parametrize("animal", ["Dragon", "Phoenix", "Unicorn", "Griffin", "Sea Serpent", "Mermaid", "Pegasus", "Hydra", "Yeti", "Werewolf"])
def test_mythical_animals_are_barred(packet, animal):
    b = good_brief()
    b["archetype"]["animal"] = animal
    b["archetype"]["justification"] = "His work is broad and no template animal is built on how he builds coalitions across the House today."
    assert ("archetype.animal", "archetype_misfit") in kinds(b, packet)


def test_consecutive_sentences_may_not_open_with_the_same_word_except_the_pronoun_twice_in_a_row(packet):
    same_word = good_brief()["speaks"].replace("Once a conversation starts, he takes charge of it.", "Once a conversation starts, he takes charge of it. Once again, he keeps the floor.")
    assert same_word != good_brief()["speaks"] and ("speaks", "repetition") in kinds(edit(speaks=same_word), packet)
    start = "Rich is skeptical by default and hard to win over. "
    end = " In hearings he is one of the least combative members of the House."
    middle = " Few match how reliably he votes and sees his bills through, and he talks about his work in much more careful, detailed terms than most."
    two = start + "He is also fairly careful and organized. He is one of the most dependable members of the House." + middle + end
    assert ("personality", "repetition") not in kinds(edit(personality=two), packet)                          # two in a row: allowed
    three = start + "He is also fairly careful and organized. He is one of the most dependable members of the House. He sees his bills through more reliably than most." + middle + end
    assert ("personality", "repetition") in kinds(edit(personality=three), packet)                           # a third in the run: barred
    separate = start + "He is also fairly careful and organized. He is one of the most dependable members of the House. Few match how reliably he votes. In hearings he is mild. He is one of the least combative members of the House."
    assert ("personality", "repetition") not in kinds(edit(personality=separate), packet)                     # two separate pairs: each run is its own
    elsewhere = good_brief()["speaks"].replace("He talks faster than most members, and his pitch swings wider than theirs, so even brief replies have life in them.",
                                                "He talks faster than most members. He lifts and drops his pitch more than theirs, so even brief replies have life in them.")
    elsewhere = elsewhere.replace("He answers briefly and cleanly, with almost no filler words,", "Expect brief, clean answers with almost no filler words,")
    assert elsewhere != good_brief()["speaks"]
    assert ("speaks", "repetition") not in kinds(edit(personality=two, speaks=elsewhere), packet)             # no brief-wide allowance any more
    his = good_brief()["speaks"].replace("He talks faster than most members, and his pitch swings wider than theirs, so even brief replies have life in them.",
                                         "His pace is quick. His pitch rises and falls more than most, so even brief replies have life in them. His timing helps.")
    assert his != good_brief()["speaks"]
    assert ("speaks", "repetition") in kinds(edit(speaks=his), packet)                                        # "He", "His", "His": three in the family


# ---- 16. the style guide heading
def test_the_style_guide_names_the_heading_as_name_dash_the_animal():
    guide = (ROOT / "fec/members/member_briefs_v3_style_guide.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| `<") and "Animal>" in line and "heading" in line)
    assert "`<Name> \u2014 The <Animal>`" in row and "<Title>" not in row
    assert "<Title>" not in guide.split("## 1. The brief at a glance", 1)[1].split("## 2.", 1)[0]


# ---- 13. no date-stamped data paths
def test_the_builder_has_no_date_stamped_data_paths_and_requires_both_inputs(tmp_path):
    import inspect
    source = inspect.getsource(P)
    assert "20261020" not in source and "melange-data" not in source
    with pytest.raises(SystemExit) as stop:
        P.main(["--out", str(tmp_path / "o.json")])
    assert stop.value.code == 2


# ---- 14. backups
def test_backup_before_write_never_overwrites_a_backup_made_with_the_same_stamp(tmp_path):
    live = tmp_path / "receipt.json"
    made = []
    for text in ("v1", "v2", "v3"):
        live.write_text(text, encoding="utf-8")
        made.append(RMP.backup_before_write(live, tmp_path / "bk", "20261020T000000Z"))
    assert len(set(made)) == 3 and [b.read_text(encoding="utf-8") for b in made] == ["v1", "v2", "v3"]
    assert made[0].name == "receipt.pre_legacy_refresh_20261020T000000Z.json" and made[1].name.endswith("-1.json") and made[2].name.endswith("-2.json")



# =========================================================================== pilot review: builder ====
def it(item_id, band):
    return {"id": item_id, "band": band, "neutral": band == "middling"}


TOP, WELL_ABOVE, ABOVE, MID, BELOW, WELL_BELOW, BOTTOM = ("among the highest in the chamber", "well above most", "above most", "middling",
                                                          "below most", "well below most", "among the lowest in the chamber")
GATES = {"DF": False, "BH.coop": False, "topic_associations": []}


def candidates(items, dims=(), gates=GATES, ps=(), rc=None, tier="standard"):
    return P.archetype_candidates(items, gates, {"dims": list(dims)} if dims else None, list(ps), rc, tier)


def cand(cands, animal):
    return next(c for c in cands if c["animal"] == animal)


# B1: HI and LO are the two outer bands
@pytest.mark.parametrize("band,hi,HI,lo,LO", [
    (TOP, True, True, False, False), (WELL_ABOVE, True, True, False, False), (ABOVE, True, False, False, False),
    (BELOW, False, False, True, False), (WELL_BELOW, False, False, True, True), (BOTTOM, False, False, True, True)])
def test_HI_and_LO_are_the_two_outer_bands(band, hi, HI, lo, LO):
    view = P._View([it("TR.E", band)], GATES, [], [], None)
    assert [view.holds("TR.E", c) for c in ("hi", "HI", "lo", "LO")] == [hi, HI, lo, LO]


def test_a_well_below_most_extraversion_now_disqualifies_terrier_and_orca_and_blue_jay():
    items = [it("TR.E", WELL_BELOW), it("TR.C", BELOW), it("IX.burst", ABOVE), it("BH.coop", BELOW)]
    cands, _ = candidates(items, dims=["outcome"])
    for animal in ("Terrier", "Orca", "Blue Jay", "Butterfly"):
        assert cand(cands, animal)["disqualified"], animal
    cands, _ = candidates([it("TR.E", BELOW)], dims=["outcome"])
    assert not cand(cands, "Terrier")["disqualified"]                           # below most is not an outer band


# B2: the tie-break picks among the animals that match the appeal
def balint_like():
    return [it("TR.E", TOP), it("TR.O", TOP), it("QR.antagonism", BOTTOM), it("FC.excitement", ABOVE), it("BH.dominance", TOP),
            it("TR.N", TOP), it("QR.affect", TOP), it("FC.warmth", WELL_ABOVE), it("IX.cutoff", ABOVE), it("AU.volume", TOP)]


def test_the_tie_break_never_picks_an_animal_that_fails_the_appeal_test():
    items = balint_like()
    cands, view = candidates(items, dims=["community"])
    by = {c["animal"]: c["matched"] for c in cands}
    assert by["Blue Jay"] == 6 and by["Hummingbird"] == 5 and by["Bear"] == 5
    assert not P._appeal_matched(cand(cands, "Blue Jay"), view) and P._appeal_matched(cand(cands, "Bear"), view)
    assert P._outer_explained(cand(cands, "Blue Jay"), view) > P._outer_explained(cand(cands, "Bear"), view)          # why the old max() went wrong
    assert P.choose_animal(cands, view, "standard")["animal"] == "Bear"


# B3: the outer-band count counts one item per matched slot, in the right direction only
def test_outer_explained_counts_one_per_slot_and_ignores_items_pointing_the_wrong_way():
    items = [it("TR.E", BOTTOM), it("TR.C", WELL_ABOVE), it("VI.observed", BOTTOM), it("VI.role", BOTTOM), it("HA", BOTTOM), it("BH.dominance", BELOW)]
    cands, view = candidates(items)
    tortoise = cand(cands, "Tortoise")
    assert tortoise["matched_slots"] == [1, 2, 3, 5]
    assert P._outer_explained(tortoise, view) == 2                               # slot 1 (TR.E) and the ONE merged low-profile slot
    items = [it("FC.warmth", WELL_ABOVE), it("FC.gregariousness", BOTTOM)]       # the lowest gregariousness points the wrong way for a "hi" slot
    cands, view = candidates(items, dims=["community"])
    hummingbird = cand(cands, "Hummingbird")
    assert hummingbird["matched_slots"] == [1, 2] and P._outer_explained(hummingbird, view) == 0
    items = [it("FC.warmth", WELL_ABOVE), it("TR.E", ABOVE), it("FC.gregariousness", BOTTOM)]     # slot 3 matches through TR.E; the lowest gregariousness points away
    cands, view = candidates(items, dims=["community"])
    hummingbird = cand(cands, "Hummingbird")
    assert hummingbird["matched_slots"] == [1, 2, 3] and P._outer_explained(hummingbird, view) == 0 and hummingbird["outer"] == 0


# B4: a strongly opposite slot disqualifies every animal
@pytest.mark.parametrize("animal,items,slot", [
    ("Orca", [it("TR.O", BOTTOM)], 3), ("Butterfly", [it("VI.observed", BOTTOM), it("VI.role", BOTTOM)], 5),
    ("Owl", [it("VI.role", BOTTOM)], 6), ("Tortoise", [it("TR.C", BOTTOM), it("QR.diligence", BOTTOM), it("BH.diligence", BOTTOM)], 2),
    ("Bull", [it("TR.C", BOTTOM), it("BH.diligence", BOTTOM)], 3)])
def test_a_strongly_opposite_slot_disqualifies_every_animal(animal, items, slot):
    cands, _ = candidates(items)
    assert "slot %d strongly opposite" % slot in cand(cands, animal)["contradicted"] and cand(cands, animal)["disqualified"]


def test_strongly_opposite_needs_every_present_alternative_two_bands_the_other_way():
    cands, _ = candidates([it("VI.observed", TOP), it("VI.role", BELOW)])              # Tortoise slot 5: one alternative still matches
    assert "slot 5 strongly opposite" not in cand(cands, "Tortoise")["contradicted"]
    cands, _ = candidates([it("VI.observed", ABOVE)])                                  # one band the other way is not "strongly"
    assert "slot 5 strongly opposite" not in cand(cands, "Tortoise")["contradicted"]
    cands, _ = candidates([it("VI.observed", TOP), it("VI.role", WELL_ABOVE)])         # every present alternative two or more bands opposite
    assert "slot 5 strongly opposite" in cand(cands, "Tortoise")["contradicted"]
    cands, _ = candidates([])                                                          # nothing present: neither matched nor opposite
    assert not any("strongly opposite" in x for c in cands for x in c["contradicted"])
    cands, _ = candidates([it("DF", WELL_ABOVE)], gates={"DF": True, "BH.coop": False, "topic_associations": []})   # DF "present" has no direction
    assert not any("strongly opposite" in x for c in cands for x in c["contradicted"])


def tortoise_base():
    return [it("TR.C", WELL_ABOVE), it("BH.diligence", ABOVE), it("TR.E", BOTTOM), it("HA", BELOW)]                # Tortoise: slots 1, 2, 5


def test_the_themes_follow_the_guide_table():
    assert {t: kind for t, (kind, _) in P.THEMES.items()} == {
        "cooperative": "behavior", "combative": "behavior", "expressive": "behavior", "reserved": "behavior", "careful": "behavior",
        "loose": "behavior", "wide": "range", "narrow": "range"}
    assert P.THEMES["cooperative"][1] == [("BH.coop", 1), ("TR.A", 1), ("QR.antagonism", 1), ("FC.warmth", 1), ("HA", -1)]


def test_one_unexplained_item_is_advisory_and_two_in_a_behavior_theme_force_a_coin():
    cands, view = candidates(tortoise_base() + [it("BH.coop", TOP)])
    coin, chosen = P.coin_recommendation(cands, view, "standard")
    assert chosen["animal"] == "Tortoise" and coin["value"] is False
    assert coin["binding_unexplained"] == {} and coin["advisory_unexplained"] == {"cooperative": ["BH.coop"]} and coin["registry_match"] is None
    cands, view = candidates(tortoise_base() + [it("BH.coop", TOP), it("FC.warmth", TOP)])                           # warmth is a cooperative item
    coin, chosen = P.coin_recommendation(cands, view, "standard")
    assert chosen["animal"] == "Tortoise" and coin["value"] is True and "cooperative (" in coin["reason"]
    assert coin["binding_unexplained"] == {"cooperative": ["BH.coop", "FC.warmth"]}
    assert coin["advisory_unexplained"] == {"expressive": ["FC.warmth"]}                                            # warmth alone is one expressive item: advisory


def test_a_range_theme_is_advisory_however_many_items_it_has():
    cands, view = candidates(tortoise_base() + [it("QR.breadth", TOP), it("BH.breadth", TOP)])
    coin, chosen = P.coin_recommendation(cands, view, "standard")
    assert chosen["animal"] == "Tortoise" and coin["value"] is False
    assert coin["binding_unexplained"] == {} and coin["advisory_unexplained"] == {"wide": ["BH.breadth", "QR.breadth"]}


def test_a_coin_for_no_template_animal_carries_empty_theme_lists():
    cands, view = candidates([])
    coin, chosen = P.coin_recommendation(cands, view, "standard")
    assert chosen is None and coin["value"] is True and coin["binding_unexplained"] == {} and coin["advisory_unexplained"] == {}
    assert coin["registry_match"] is None


def test_items_the_chosen_animal_explains_do_not_count_as_unexplained():
    items = [it("TR.C", TOP), it("BH.diligence", TOP), it("QR.diligence", TOP), it("TR.E", BOTTOM), it("HA", BELOW)]   # "careful" x3, all in Tortoise slot 2
    cands, view = candidates(items)
    coin, chosen = P.coin_recommendation(cands, view, "standard")
    assert chosen["animal"] == "Tortoise" and coin["value"] is False and coin["advisory_unexplained"] == {}


# B6 (guide 5.7, 13): a mixed appeal keeps its item but gives no advice; an axis below most can never be led with
def test_a_mixed_appeal_keeps_its_item_gives_no_advice_and_cannot_be_cited():
    got = P.normalize_appeals({"assertive_vs_communicative_frame": MIXED[0], "supported_dimensions": [],
                               "agency_percentile": .9, "communion_percentile": .9})
    assert got["dims"] == ["mixed"] and got["plain"] == "no single frame leads; give no framing advice"
    assert got["lead_ok"] == {"results": False, "people": False}                           # both false for mixed, whatever the percentiles
    thin = build()[0]["Beth Thin"]                                                          # the fixture's thin member is "mixed"
    ma = next(i for i in thin["evidence"] if i["id"] == "MA")
    assert thin["appeals"]["dims"] == ["mixed"] and ma["may_support"] == [] and ma["plain"] == got["plain"]
    view = P._View([], GATES, ["mixed"], [], None)
    assert view.dims == set() and not any(view.holds("MA", d) for d in ("outcome", "community", "technical", "partnership"))


@pytest.mark.parametrize("frame,supported,agency,communion,results,people", [
    (OUTCOME[0], OUTCOME[1], .30, .5, False, True), (COMMUNITY[0], COMMUNITY[1], .5, .41, True, False),
    ("Direct style: be crisp", OUTCOME[1], .41, .5, False, True), ("Partnership framing: offer", OUTCOME[1] + COMMUNITY[1], .9, .30, True, False),
    (OUTCOME[0], OUTCOME[1], .9, .42, True, True), (COMMUNITY[0], COMMUNITY[1], None, .9, True, True)])
def test_lead_ok_is_false_for_an_axis_under_the_below_most_cut(frame, supported, agency, communion, results, people):
    got = P.normalize_appeals({"assertive_vs_communicative_frame": frame, "supported_dimensions": supported,
                               "agency_percentile": agency, "communion_percentile": communion})
    assert got["lead_ok"] == {"results": results, "people": people} and got["dims"] != ["mixed"]


def test_the_packet_hash_follows_lead_ok_but_not_the_percentiles_behind_it(packet):
    q = copy.deepcopy(packet)
    q["appeals"]["lead_ok"] = {"results": not packet["appeals"]["lead_ok"]["results"], "people": packet["appeals"]["lead_ok"]["people"]}
    assert P.content_sha256(q) != packet["packet_sha256"]


# guide 8.1: a matched slot is never strongly opposite
def test_a_matched_slot_is_never_strongly_opposite():
    items = [it("BH.dominance", BOTTOM)]                                                    # Blue Jay slot 5: BH.dominance hi or DF
    cands, _ = candidates(items, gates={"DF": True, "BH.coop": False, "topic_associations": []})
    assert 5 in cand(cands, "Blue Jay")["matched_slots"] and "slot 5 strongly opposite" not in cand(cands, "Blue Jay")["contradicted"]
    cands, _ = candidates(items)
    assert 5 not in cand(cands, "Blue Jay")["matched_slots"] and "slot 5 strongly opposite" in cand(cands, "Blue Jay")["contradicted"]


# guide 8.1: Lone Wolf is not for a member near the top of the chamber for cross-party work
def test_lone_wolf_is_disqualified_for_a_well_above_most_cross_party_member():
    ps = [{"category": "formative"}]
    plain, _ = candidates(lone_wolf_items() + [it("BH.coop", ABOVE)], ps=ps, rc="less")
    assert not cand(plain, "Lone Wolf")["disqualified"]
    for band in (WELL_ABOVE, TOP):
        coop, _ = candidates(lone_wolf_items() + [it("BH.coop", band)], ps=ps, rc="less")
        assert "BH.coop HI" in cand(coop, "Lone Wolf")["contradicted"] and cand(coop, "Lone Wolf")["disqualified"]


def test_every_candidate_carries_its_outer_count():
    cands, view = candidates(balint_like(), dims=["community"])
    assert all(c["outer"] == P._outer_explained(c, view) for c in cands) and cand(cands, "Blue Jay")["outer"] > 0


# guide 8.2 step 4, on a small made-up table so each rule is isolated
def hi(*ids):
    return [[(i, "hi")] for i in ids]


def world(monkeypatch, animals, items, dims=()):
    monkeypatch.setattr(P, "ARCHETYPES", {a: {"descriptor": "x and y", "slots": slots, "contra": []} for a, slots in animals.items()})
    return candidates(items, dims=dims)


FIVE = ["TR.E", "TR.C", "TR.O", "TR.A", "TR.N"]
WARM = ["FC.warmth", "FC.excitement", "FC.gregariousness", "FC.assertiveness"]


@pytest.mark.parametrize("outer_items,expected", [(1, "Aardvark"), (2, "Badger")])
def test_a_runner_up_replaces_the_leader_only_when_it_explains_two_more_outer_band_items(monkeypatch, outer_items, expected):
    items = [it(i, ABOVE) for i in FIVE] + [it(i, TOP if n < outer_items else ABOVE) for n, i in enumerate(WARM)]
    cands, view = world(monkeypatch, {"Aardvark": hi(*FIVE), "Badger": hi(*WARM, "BH.coop")}, items)
    by = {c["animal"]: c for c in cands}
    assert (by["Aardvark"]["matched"], by["Badger"]["matched"], by["Badger"]["outer"], by["Aardvark"]["outer"]) == (5, 4, outer_items, 0)
    assert P.choose_animal(cands, view, "standard")["animal"] == expected


def test_a_runner_up_is_in_the_tie_group_only_from_four_matched_slots(monkeypatch):
    items = [it(i, ABOVE) for i in ("TR.E", "TR.C", "TR.O")] + [it(i, TOP) for i in WARM[:3]]
    cands, view = world(monkeypatch, {"Aardvark": hi("TR.E", "TR.C", "TR.O", "TR.A"), "Badger": hi(*WARM[:3], "BH.coop")}, items)
    assert (cand(cands, "Aardvark")["matched"], cand(cands, "Badger")["matched"]) == (3, 3)
    assert P.choose_animal(cands, view, "standard")["animal"] == "Badger"                    # equal slot counts: more outer-band items explained wins
    items = [it(i, ABOVE) for i in FIVE[:4]] + [it(i, TOP) for i in WARM[:3]]
    cands, view = world(monkeypatch, {"Aardvark": hi(*FIVE[:4]), "Badger": hi(*WARM[:3], "BH.coop")}, items)
    assert (cand(cands, "Aardvark")["matched"], cand(cands, "Badger")["matched"]) == (4, 3)
    assert P.choose_animal(cands, view, "standard")["animal"] == "Aardvark"                  # three is not a runner-up: only four or more join the group


@pytest.mark.parametrize("dims,expected", [(["outcome"], "Ferret"), (["mixed"], "Ferret"), ([], "Ferret"), (["community"], "Eland")])
def test_an_animal_whose_appeal_slot_names_another_dimension_drops_out_of_a_tie(monkeypatch, dims, expected):
    items = [it(i, ABOVE) for i in ("TR.E", "TR.C", "TR.O")]
    animals = {"Eland": hi("TR.E", "TR.C", "TR.O") + [[("MA", "community")]], "Ferret": hi("TR.E", "TR.C", "TR.O")}
    cands, view = world(monkeypatch, animals, items, dims=dims)
    assert P.choose_animal(cands, view, "standard")["animal"] == expected


def test_if_the_appeal_rule_would_drop_every_tied_animal_it_drops_none(monkeypatch):
    items = [it(i, ABOVE) for i in ("TR.E", "TR.C", "TR.O")]
    animals = {"Heron": hi("TR.E", "TR.C", "TR.O") + [[("MA", "technical")]], "Eland": hi("TR.E", "TR.C", "TR.O") + [[("MA", "community")]]}
    cands, view = world(monkeypatch, animals, items, dims=["outcome"])
    assert P.choose_animal(cands, view, "standard")["animal"] == "Eland"                     # both dropped, so both kept: first alphabetically


# B7: Lone Wolf is for independent members, not elected party leaders
def lone_wolf_items():
    return [it("TR.A", WELL_BELOW), it("IX.wpt_mean", ABOVE), it("HA", ABOVE)]


def test_lone_wolf_is_disqualified_for_an_elected_party_leader():
    ps = [{"category": "formative"}]
    plain, view = candidates(lone_wolf_items(), ps=ps, rc="less")
    assert not cand(plain, "Lone Wolf")["disqualified"] and cand(plain, "Lone Wolf")["matched"] >= 4
    leader, _ = candidates(lone_wolf_items() + [{"id": "LD", "band": None, "neutral": False}], ps=ps, rc="less")
    assert cand(leader, "Lone Wolf")["disqualified"] and "LD present" in cand(leader, "Lone Wolf")["contradicted"]


def test_a_packet_for_a_leader_carries_the_leadership_item_the_rule_reads():
    led = ledger()
    rec = {**led["members"]["Alex Rich"], "name": "Alex Rich", "leadership_audio_source_policy": LEADER}
    led["members"] = {"Alex Rich": rec}
    packets, _ = P.build_all(led, legislators=LEGS, congestion=CONG, model_bio={}, desk_src=DESK, wiki={}, basis_counts=COUNTS, levels_doc=levels())
    assert "LD" in {i["id"] for i in packets["Alex Rich"]["evidence"]}


# ---- the appeal percentiles of James and Boozman are tied real values, not imputed defaults
def test_identical_appeal_percentiles_come_from_tied_raw_values_not_defaults():
    path = ROOT / "data/members/agency_communion.json"
    if not path.is_file():
        pytest.skip("agency_communion.json not present")
    rows = json.loads(path.read_text(encoding="utf-8"))["members"]
    james, boozman = rows["John James"], rows["John Boozman"]
    assert (james["agency"], james["communion"]) == (boozman["agency"], boozman["communion"]) == (0.0625, 0.15)
    assert james["n"] == boozman["n"] == 40 and james["communion_raw"] != boozman["communion_raw"]       # different raw shares, same rounded score
    tied = sum(1 for r in rows.values() if (r.get("agency"), r.get("communion")) == (0.0625, 0.15))
    assert tied >= 6                                                                                       # a coarse 40-sentence scale: many members tie


# =========================================================================== pilot review: validator ====
def unique_brief(i, shared):
    letters = lambda n: "".join(chr(97 + int(c)) for c in str(n))            # the n-gram tokenizer keeps letters only
    words = lambda tag, n: " ".join("%s%s%s" % (tag, letters(i), letters(j)) for j in range(n))
    return {"archetype": {"animal": "Owl", "descriptor": "careful and calm", "justification": words("j", 14)},
            "bottom_line": words("b", 16), "do": [words("d%d" % k, 5) for k in range(4)], "dont": [words("n%d" % k, 5) for k in range(3)],
            "warning_sign": words("w", 30), "follow_up": words("f", 18), "speaks": words("s", 70), "decides": words("e", 70),
            "personality": "Name%s %s %s%s" % (letters(i), words("p", 70), words("q", 3), " Bring the one fact that settles the question quickly." if shared else "")}


def corpus_flags(n_total, n_sharing):
    packets, _ = build()
    briefs = {}
    for i in range(n_total):
        key = "M%d" % i
        briefs[key] = unique_brief(i, i < n_sharing)
        packets[key] = {**packets["Alex Rich"], "member": {**packets["Alex Rich"]["member"], "surname": "Name%d" % i, "display_name": key}}
    return {n for n, v in V.corpus_issues(briefs, packets).items() if any("six-word phrases" in x["detail"] for x in v)}


def test_corpus_repetition_needs_both_two_percent_and_three_briefs():
    assert corpus_flags(15, 2) == set()              # 13 percent of briefs, but only two briefs: allowed
    assert len(corpus_flags(15, 3)) == 3             # three briefs and over two percent: fails
    assert corpus_flags(200, 3) == set()             # three briefs, but under two percent: allowed
    assert len(corpus_flags(100, 3)) == 3 and corpus_flags(100, 2) == set()


def example_files(tmp_path, monkeypatch, gold_sentence=None, prompt_sentence=None):
    gold = tmp_path / "exemplars.json"
    gold.write_text(json.dumps({"exemplars": [{"display_name": "Pat Gold", "personality": gold_sentence or "Gold is a fixed point in every meeting he joins.",
                                               "evidence_refs": {"x": ["not text"]}}]}), encoding="utf-8")
    prompt = tmp_path / "writer_prompt.md"
    prompt.write_text("# prompt\n\n```\n" + json.dumps({"personality": prompt_sentence or "Quill reads every line of a bill before the first meeting.",
                                                         "evidence_refs": {"y": ["no"]}}) + "\n```\n", encoding="utf-8")
    monkeypatch.setattr(P, "EXEMPLARS_PATH", gold)
    monkeypatch.setattr(V, "PROMPT_PATH", prompt)
    for cached in (V.example_grams, V.example_texts, V.example_rows):
        cached.cache_clear()


def test_six_words_copied_from_the_writer_prompt_or_a_gold_exemplar_fail(packet, tmp_path, monkeypatch):
    example_files(tmp_path, monkeypatch)
    for copied in ("is a fixed point in every meeting", "reads every line of a bill before"):
        got = kinds(edit(personality=good_brief()["personality"] + " He " + copied + " today."), packet)
        assert ("personality", "copied_text") in got, copied
    near = good_brief()["personality"] + " He is a fixed point in some meetings."                   # five words in a row: below the example limit
    assert ("personality", "copied_text") not in kinds(edit(personality=near), packet)
    V.example_grams.cache_clear()


def test_a_gold_exemplar_may_repeat_its_own_text_but_no_other_member_may(packet, tmp_path, monkeypatch):
    own = "Rich is a fixed point in every meeting he joins and reads every line."
    example_files(tmp_path, monkeypatch, gold_sentence=own)
    brief = edit(personality=own + " " + good_brief()["personality"])
    assert ("personality", "copied_text") not in kinds(brief, packet)                    # the packet's member is "Rich": this is their own gold
    other = copy.deepcopy(packet)
    other["member"]["surname"] = "Other"
    assert ("personality", "copied_text") in kinds(brief, other)
    V.example_grams.cache_clear()


def test_the_real_writer_prompt_examples_are_read_and_the_exemplar_file_is_optional(monkeypatch, tmp_path):
    V.example_grams.cache_clear()
    monkeypatch.setattr(P, "EXEMPLARS_PATH", tmp_path / "missing.json")
    grams = V.example_grams()
    assert any("jeffries" in k or "lucas" in k for k in grams) and all(isinstance(v, set) and v for v in grams.values())
    V.example_grams.cache_clear()


@pytest.mark.parametrize("word", ["Republican", "Democrat", "Democratic", "Republicans", "Democrats", "GOP"])
def test_party_names_are_banned_in_any_field(packet, word):
    for field, base in (("bottom_line", good_brief()["bottom_line"]), ("personality", good_brief()["personality"])):
        assert (field, "banned_phrase") in kinds(edit(**{field: base + " He is a %s from Ohio." % word}), packet)
    b = good_brief()
    b["do"][1] = "Back each point with a fact a %s can check" % word
    assert ("do.2", "banned_phrase") in kinds(b, packet)


def test_a_party_word_is_banned_even_inside_a_leadership_title_so_the_writer_uses_ld_plain(packet):
    p = copy.deepcopy(packet)
    p["member"]["leadership"] = {"title": "House Democratic Leader", "plain": "his party's House leader"}
    base = good_brief()["personality"]
    ok = edit(personality=base.replace("He ranks among the most visible", "He is his party's House leader and ranks among the most visible"))
    assert ("personality", "banned_phrase") not in kinds(ok, p)
    bad = edit(personality=base.replace("He ranks among the most visible", "He is House Democratic Leader and ranks among the most visible"))
    assert ("personality", "banned_phrase") in kinds(bad, p)


@pytest.mark.parametrize("phrase", ["Few members lead as he does", "Few are less combative", "He is as quick as anyone there",
                                    "He is one of the most careful members", "He takes fewer calls than nearly anyone", "He is quicker than almost anyone"])
def test_top_band_phrases_need_a_top_or_bottom_band_item_in_that_field(packet, phrase):
    items = {i["id"]: i for i in packet["evidence"]}
    top_ids = [i for i, x in items.items() if x.get("band") in (TOP, BOTTOM)]
    low_ids = [i for i, x in items.items() if x.get("band") in (BELOW, ABOVE, WELL_ABOVE)]
    assert top_ids and len(low_ids) >= 2
    weak = good_brief()
    weak["bottom_line"] = "Walk in with a short, fact-heavy case and expect him to test it. %s." % phrase
    weak["evidence_refs"]["bottom_line"] = low_ids[:1] + ["MA"]
    assert ("bottom_line", "wrong_strength") in kinds(weak, packet)
    strong = copy.deepcopy(weak)
    strong["evidence_refs"]["bottom_line"] = [top_ids[0], "MA"]
    assert ("bottom_line", "wrong_strength") not in kinds(strong, packet)


@pytest.mark.parametrize("phrase", ["His record is rare", "He seldom changes course", "He seldom asks twice", "He does little to hide it",
                                    "He sits at the low end of the House", "Little moves him"])
def test_little_rare_seldom_need_a_well_below_most_item_in_that_field(packet, phrase):
    items = {i["id"]: i for i in packet["evidence"]}
    well_low = [i for i, x in items.items() if x.get("band") in (WELL_BELOW, BOTTOM)]
    other = [i for i, x in items.items() if x.get("band") in (ABOVE, WELL_ABOVE, TOP) or x.get("band") == BELOW]
    assert well_low and other
    weak = edit(follow_up="Send a short memo within a day that answers each objection. %s." % phrase)
    weak["evidence_refs"]["follow_up"] = other[:1] + ["DF"]
    assert ("follow_up", "wrong_strength") in kinds(weak, packet)
    strong = copy.deepcopy(weak)
    strong["evidence_refs"]["follow_up"] = ["DF"] + well_low[:1]
    assert ("follow_up", "wrong_strength") not in kinds(strong, packet)


def test_a_little_as_the_below_most_phrase_is_not_a_strength_word(packet):
    b = edit(follow_up="Send a short memo within a day, and expect him to be a little slow to answer it, with a source for every fact.")
    assert ("follow_up", "wrong_strength") not in kinds(b, packet)


def test_an_audio_id_in_a_do_or_a_dont_still_needs_an_ix_bh_or_df_companion(packet):
    for key in ("do.1", "do.3", "dont.2"):
        b = good_brief()
        b["evidence_refs"][key] = ["AU.pace"]
        assert ("evidence_refs." + key, "ref_wrong") in kinds(b, packet), key
        b["evidence_refs"][key] = ["AU.pace", "IX.discmark"]
        assert ("evidence_refs." + key, "ref_wrong") not in kinds(b, packet), key


# =========================================================================== guide 8 and 14 alignment: validator ====
def test_the_pronoun_family_may_open_two_sentences_in_a_row_but_not_three(packet):
    head = "He answers briefly and cleanly, with almost no filler words, fewer than nearly anyone in the House."
    mid = " His pace is quick and his pitch moves more than most, so even brief replies have life in them."
    rest = " Challenge him and he defends his position, show after show. His word choice carries little emotional charge, so the pressure comes from his habits, not his vocabulary."
    two = head + mid + " Once a conversation starts, he takes charge of it." + rest                  # He, His, Once: two in the family
    three = head + mid + " He takes charge once a conversation starts." + rest                         # He, His, He: three in the family
    assert ("speaks", "repetition") not in kinds(edit(speaks=two), packet)
    assert ("speaks", "repetition") in kinds(edit(speaks=three), packet)


def test_bullets_are_read_as_a_list_for_repeated_openers(packet):
    b = good_brief()
    b["do"][1] = "Name the source behind every claim you make"                                      # do.1 and do.2 both open with "Name"
    assert ("do.2", "repetition") in kinds(b, packet)
    b = good_brief()
    b["do"][2] = "Name the person who can confirm the figures"                                      # do.1 "Name", do.2 "Cite", do.3 "Name": not consecutive
    assert not any(k == "repetition" for _, k in kinds(b, packet))
    b = good_brief()
    b["dont"][1] = "Count on a quick yes at the first meeting"                                      # dont.1 and dont.2 both open with "Count"
    assert ("dont.2", "repetition") in kinds(b, packet)


def start_flags(n_total, n_sharing):
    letters = lambda n: "".join(chr(97 + int(c)) for c in str(n))
    packets, _ = build()
    briefs = {}
    for i in range(n_total):
        key, sur = "M%d" % i, "Zed" + letters(i)
        b = unique_brief(i, False)
        rest = " ".join(b["personality"].split()[1:])
        b["personality"] = "%s %s%s" % (sur, "is calm under pressure " if i < n_sharing else "", rest)
        briefs[key] = b
        packets[key] = {**packets["Alex Rich"], "member": {**packets["Alex Rich"]["member"], "surname": sur, "display_name": key}}
    return {n for n, v in V.corpus_issues(briefs, packets).items() if any("opening shared" in x["detail"] for x in v)}


def test_a_shared_personality_opening_needs_both_two_percent_and_three_briefs():
    assert len(start_flags(100, 3)) == 3             # three briefs, three percent: sent back
    assert start_flags(100, 2) == set()              # two briefs: allowed
    assert start_flags(200, 3) == set()              # three briefs, but under two percent


@pytest.mark.parametrize("phrase", ["Nearly everyone in the House talks less", "He is as quick as any member", "Almost no member is blunter",
                                    "He is among the sharpest members", "Few senators match his pace"])
def test_the_rest_of_the_top_band_word_list_needs_an_outer_band_item(packet, phrase):
    items = {i["id"]: i for i in packet["evidence"]}
    top_ids = [i for i, x in items.items() if x.get("band") in (TOP, BOTTOM)]
    low_ids = [i for i, x in items.items() if x.get("band") in (BELOW, ABOVE, WELL_ABOVE)]
    weak = good_brief()
    weak["bottom_line"] = "Walk in with a short, fact-heavy case and expect him to test it. %s." % phrase
    weak["evidence_refs"]["bottom_line"] = low_ids[:1] + ["MA"]
    assert ("bottom_line", "wrong_strength") in kinds(weak, packet)
    strong = copy.deepcopy(weak)
    strong["evidence_refs"]["bottom_line"] = [top_ids[0], "MA"]
    assert ("bottom_line", "wrong_strength") not in kinds(strong, packet)


@pytest.mark.parametrize("phrase", ["He hardly changes course", "He barely moves a muscle", "He rarely yields"])
def test_hardly_and_barely_join_the_low_strength_words(packet, phrase):
    items = {i["id"]: i for i in packet["evidence"]}
    well_low = [i for i, x in items.items() if x.get("band") in (WELL_BELOW, BOTTOM)]
    other = [i for i, x in items.items() if x.get("band") in (ABOVE, WELL_ABOVE, TOP)]
    weak = edit(follow_up="Send a short memo within a day that answers each objection. %s." % phrase)
    weak["evidence_refs"]["follow_up"] = other[:1] + ["DF"]
    assert ("follow_up", "wrong_strength") in kinds(weak, packet)
    strong = copy.deepcopy(weak)
    strong["evidence_refs"]["follow_up"] = ["DF"] + well_low[:1]
    assert ("follow_up", "wrong_strength") not in kinds(strong, packet)


def appeal_packet(packet, dims, results, people):
    p = copy.deepcopy(packet)
    p["appeals"] = {**p["appeals"], "dims": dims, "lead_ok": {"results": results, "people": people}}
    return p


def test_a_mixed_appeal_cannot_be_cited_in_any_field(packet):
    p = appeal_packet(packet, ["mixed"], False, False)
    ma = next(i for i in p["evidence"] if i["id"] == "MA")
    ma["may_support"], ma["plain"] = [], P.MIXED_PLAIN
    got = kinds(good_brief(), p)
    assert ("evidence_refs.archetype", "ref_not_allowed") in got and ("evidence_refs.do.1", "ref_not_allowed") in got
    clean = good_brief()
    clean["evidence_refs"] = {k: [c for c in v if c != "MA"] or ["BH.wpt"] for k, v in clean["evidence_refs"].items()}
    assert not any("MA" in i["detail"] for i in V.validate_brief(clean, p)[0])


@pytest.mark.parametrize("sentence,results,people,flagged", [
    ("Open with the results he can check line by line.", False, True, True), ("Open with the results he can check line by line.", True, True, False),
    ("Start with the human stakes and the families behind the bill.", True, False, True),
    ("Start with the human stakes and the families behind the bill.", True, True, False),
    ("Lead with the data and keep the rest short.", False, True, True), ("Build the case on outcomes he can test.", False, True, True),
    ("Lead with the people and the stories behind this bill.", False, True, False)])
def test_leading_with_an_axis_below_most_is_barred_outside_the_donts(packet, sentence, results, people, flagged):
    p = appeal_packet(packet, ["outcome"], results, people)
    b = edit(follow_up="Send a one-page memo within a day that addresses every objection he raised. " + sentence)
    assert (("follow_up", "appeal_advice") in kinds(b, p)) is flagged


def test_a_dont_may_still_warn_against_leading_with_an_axis_below_most(packet):
    p = appeal_packet(packet, ["outcome"], False, True)
    b = good_brief()
    b["dont"][0] = "Open with the results and lean on pressure"
    assert not any(k == "appeal_advice" for _, k in kinds(b, p))
    b["do"][0] = "Open with the results and then wait quietly"
    assert ("do.1", "appeal_advice") in kinds(b, p)


def leader_packet(packet):
    p = copy.deepcopy(packet)
    p["member"]["leadership"] = {"title": "House Speaker", "plain": "the House's presiding leader"}
    return p


def test_a_party_leader_is_never_a_lone_wolf_or_independent(packet):
    p = leader_packet(packet)
    leader_issue = lambda brief, pkt, field: [i for i in V.validate_brief(brief, pkt)[0] if i["field"] == field and "elected party leader" in i["detail"]]
    b = good_brief()
    b["archetype"]["animal"] = "Lone Wolf"
    assert leader_issue(b, p, "archetype.animal") and not leader_issue(b, packet, "archetype.animal")      # a member without a leadership role is not barred
    for descriptor in ("principled and independent", "lone and skeptical"):
        b = good_brief()
        b["archetype"]["descriptor"] = descriptor
        assert leader_issue(b, p, "archetype.descriptor") and not leader_issue(b, packet, "archetype.descriptor")


def test_a_state_name_may_appear_once_and_only_in_personality(packet):
    assert ("bottom_line", "name_use") in kinds(edit(bottom_line=good_brief()["bottom_line"].replace("does.", "does in Ohio.")), packet)
    once = edit(personality=good_brief()["personality"] + " He is proud of Ohio.")
    assert ("personality", "name_use") not in kinds(once, packet)
    twice = edit(personality=good_brief()["personality"] + " He is proud of Ohio and Texas.")
    assert ("personality", "name_use") in kinds(twice, packet)
    west = edit(personality=good_brief()["personality"] + " He is proud of West Virginia.")            # one state, not two
    assert ("personality", "name_use") not in kinds(west, packet)


def test_eager_needs_a_high_extraversion_read(packet):
    b = good_brief()
    b["archetype"]["descriptor"] = "eager and skeptical"
    assert ("archetype.descriptor", "descriptor_unsupported") in kinds(b, packet)               # TR.E is middling for this member
    p = copy.deepcopy(packet)
    for item in p["evidence"]:
        if item["id"] == "TR.E":
            item.update(band=ABOVE, neutral=False, direction="high")
    assert ("archetype.descriptor", "descriptor_unsupported") not in kinds(b, p)


# =========================================================================== rulings: checks 11, 12, 19, 21 ====
def test_five_words_copied_from_a_plain_phrase_fail_when_they_carry_content(packet):
    p = copy.deepcopy(packet)
    p["evidence"][0]["plain"] = "keeps a tight grip on every meeting he joins"
    assert ("personality", "copied_text") in kinds(edit(personality=good_brief()["personality"] + " He keeps a tight grip on timing."), p)
    p["evidence"][0]["plain"] = "he is one of the quickest talkers"                            # "he is one of the" is all function words
    assert ("personality", "copied_text") not in kinds(edit(personality=good_brief()["personality"] + " He is one of the people who ask first."), p)
    p["appeals"]["plain"] = "bring the short numbers early"
    assert ("follow_up", "copied_text") in kinds(edit(follow_up=good_brief()["follow_up"] + " Bring the short numbers early."), p)
    assert ("follow_up", "copied_text") not in kinds(edit(follow_up=good_brief()["follow_up"] + " Bring the short numbers."), p)               # four words


def test_a_members_own_gold_text_is_no_longer_exempt_from_the_plain_copy_check(packet, tmp_path, monkeypatch):
    example_files(tmp_path, monkeypatch, gold_sentence="Rich keeps a tight grip on timing every day.")
    p = copy.deepcopy(packet)
    p["evidence"][0]["plain"] = "keeps a tight grip on every meeting he joins"
    brief = edit(personality=good_brief()["personality"] + " He keeps a tight grip on meetings.")
    assert ("personality", "copied_text") in kinds(brief, p)                                      # the packet's member is "Rich", and still fails
    assert not hasattr(V, "own_gold_grams")


def leader_with_plain(packet, plain="chair of his party's House caucus"):
    p = copy.deepcopy(packet)
    p["member"]["leadership"] = {"title": "House Democratic Caucus Chair", "plain": plain}
    p["evidence"].append({"id": "LD", "label": "leadership role", "band": None, "neutral": False, "claim": "House Democratic Caucus Chair",
                          "plain": plain, "may_support": ["personality", "decides"]})
    return p


def test_a_brief_may_copy_the_ld_title_verbatim_but_no_other_plain_phrase(packet):
    p = leader_with_plain(packet)
    title = edit(personality=good_brief()["personality"] + " He is chair of his party's House caucus.")
    assert ("personality", "copied_text") not in kinds(title, p)                                 # the guide requires LD.plain as written
    other = leader_with_plain(packet)
    other["evidence"][0]["plain"] = "chair of his party's House caucus"                         # the same words on any other item are a copy
    assert ("personality", "copied_text") in kinds(title, other)
    p["evidence"][1]["plain"] = "keeps a tight grip on every meeting he joins"
    assert ("personality", "copied_text") in kinds(edit(personality=good_brief()["personality"] + " He keeps a tight grip on timing."), p)


def coin_packet(packet, *, match=None, registry=()):
    p = copy.deepcopy(packet)
    p["coin_recommended"].update(value=True, template_coin=True, registry_match=match)
    p["coined_animals"] = list(registry)
    return p


def archetype_with(animal, descriptor="blunt and thorough"):
    b = copy.deepcopy(good_brief())
    b["archetype"].update(animal=animal, descriptor=descriptor)
    return b


def animal_issues(brief, p):
    return [i["detail"] for i in V.validate_brief(brief, p)[0] if i["field"] == "archetype.animal"]


def test_check_12_a_clear_call_must_name_the_suggested_animal(packet):
    assert packet["coin_recommended"]["value"] is False and packet["suggested_animal"] == "Bull"
    assert animal_issues(archetype_with("Bull"), packet) == []
    assert any("must be Bull" in d for d in animal_issues(archetype_with("Owl"), packet))
    assert any("must be Bull" in d for d in animal_issues(archetype_with("Badger"), packet))


def test_check_12_a_registry_match_must_be_named(packet):
    match = {"animal": "Beaver", "descriptor": "cooperative and wide-ranging"}
    p = coin_packet(packet, match=match, registry=SEED)
    assert animal_issues(archetype_with("Beaver", "cooperative and wide-ranging"), p) == []
    assert any("must be Beaver" in d for d in animal_issues(archetype_with("Bull"), p))
    assert any("must be Beaver" in d for d in animal_issues(archetype_with("Badger"), p))


def test_check_12_a_coin_takes_a_new_name_or_a_registry_animal_whose_signature_holds_never_a_template_animal(packet):
    p = coin_packet(packet, registry=SEED)
    assert animal_issues(archetype_with("Badger"), p) == []                                       # new name, not in the registry
    assert any("template" in d for d in animal_issues(archetype_with("Owl"), p))
    holds = {"animal": "Mole", "descriptor": "d", "image": "i", "signature": [["TR.A", "lo"], ["BH.diligence", "hi"]], "first_member": "x"}
    differs = {"animal": "Vole", "descriptor": "d", "image": "i", "signature": [["TR.A", "hi"], ["BH.diligence", "lo"]], "first_member": "y"}
    p = coin_packet(packet, registry=[holds, differs])
    assert animal_issues(archetype_with("Mole"), p) == []                                         # a registry animal whose signature holds
    assert any("signature" in d for d in animal_issues(archetype_with("Vole"), p))                 # a registered name with a different signature


def test_check_12_applies_to_a_thin_template_animal_with_a_registry_match(packet):
    match = {"animal": "Ox", "descriptor": "practical and narrowly focused"}
    p = copy.deepcopy(packet)
    p["coin_recommended"].update(value=True, template_coin=False, registry_match=match)
    p["coined_animals"] = list(SEED)
    assert animal_issues(archetype_with("Ox"), p) == [] and any("must be Ox" in d for d in animal_issues(archetype_with("Bull"), p))


OBSERVED_MARKERS = ["usually", "usual", "normally", "normal", "by default", "as a rule"]


@pytest.mark.parametrize("marker", OBSERVED_MARKERS)
def test_an_observed_warning_sign_needs_a_usual_pattern_marker(packet, marker):
    assert packet["warning_form"] == "observed"
    text = "He is %s quick to answer your case. If he stops and keeps restating his own view, the room has turned into a contest. Hold back new points and offer one fact." % marker
    assert ("warning_sign", "warning_form") not in kinds(edit(warning_sign=text), packet)
    bare = text.replace(" %s" % marker, "")
    assert ("warning_sign", "warning_form") in kinds(edit(warning_sign=bare), packet)


def test_an_observed_warning_sign_must_cite_a_cue_eligible_id(packet):
    for refs, ok in ((["DF", "MA"], True), (["IX.discmark"], True), (["BH.dominance", "RC"], True), (["HA"], True),
                     (["MA", "RC"], False), (["BH.diligence", "MA"], False), (["BH.coop", "BH.diligence"], False)):
        b = good_brief()
        b["evidence_refs"]["warning_sign"] = refs
        assert (("warning_sign", "warning_form") not in kinds(b, packet)) is ok, refs


def test_a_neutral_or_provisional_cue_does_not_count(packet):
    p = copy.deepcopy(packet)
    for i in p["evidence"]:
        if i["id"] == "BH.wpt":
            i["neutral"] = True
    assert "BH.wpt" not in V.cue_ids(p) and "DF" in V.cue_ids(p) and "IX.defense" in V.cue_ids(p)
    p["gates"] = {**p["gates"], "DF": False}
    assert "DF" not in V.cue_ids(p)
    p["longform"] = [{"id": "LF.t.1", "status": "qualified"}, {"id": "LF.t.2", "status": "provisional"}, {"id": "LF.t", "status": "turn"}]
    assert {"LF.t.1"} <= V.cue_ids(p) and not {"LF.t.2", "LF.t"} & V.cue_ids(p)


def test_a_procedural_warning_sign_cites_proc_and_never_says_means(packet):
    p = copy.deepcopy(packet)
    p["warning_form"] = "procedural"
    text = "A quiet meeting tells you little about where the ask stands, so end it by confirming the owner and the date. Offer a short written recap if no date comes."
    ok = edit(warning_sign=text)
    ok["evidence_refs"]["warning_sign"] = ["PROC"]
    assert ("warning_sign", "warning_form") not in kinds(ok, p)
    no_proc = copy.deepcopy(ok)
    no_proc["evidence_refs"]["warning_sign"] = ["DF"]
    assert ("warning_sign", "warning_form") in kinds(no_proc, p)
    means = edit(warning_sign=text.replace("tells you little", "means nothing yet"))
    means["evidence_refs"]["warning_sign"] = ["PROC"]
    assert ("warning_sign", "warning_form") in kinds(means, p)
    assert ("warning_sign", "warning_form") not in kinds(good_brief(), {k: v for k, v in packet.items() if k != "warning_form"})        # a packet without the field is not checked


def test_two_do_bullets_sharing_four_content_words_are_a_warning_only(packet):
    b = good_brief()
    assert not [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "do_duplication"]
    b["do"][3] = "Back each claim with a checkable source you can name"          # shares claim, checkable, source, you with do.2
    issues, warnings = V.validate_brief(b, packet)
    assert [w["field"] for w in warnings if w["kind"] == "do_duplication"] == ["do.4"] and not [i for i in issues if i["kind"] == "do_duplication"]
    b["do"][3] = "Back each claim with a checkable source from the file"       # claim, checkable, source: three shared words are not enough
    assert not [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "do_duplication"]


# =========================================================================== long-form era gating (validator) ====
LF_ALL_FIELDS = ["archetype", "bottom_line", "do", "dont", "warning_sign", "follow_up", "personality", "speaks", "decides"]


def lf_packet(packet, era, status="qualified"):
    p = copy.deepcopy(packet)
    p["longform"] = [{"id": "LF.t-01.1", "status": status, "era": era, "finding": "f", "meeting_implication": "m", "scope": "s",
                      "source_text": "Podcast, 2022", "may_support": list(LF_ALL_FIELDS)}]          # a packet that (wrongly) allows every field: the validator decides alone
    return p


def cite_lf(key, brief=None):
    b = brief or good_brief()
    b["evidence_refs"][key] = list(b["evidence_refs"][key]) + ["LF.t-01.1"]
    return b


@pytest.mark.parametrize("era", ["PRE_CONGRESS_LOCAL_OFFICE_GWINNETT_SCHOOL_BOARD", "PRE_CURRENT_LEADERSHIP_ROLE"])
@pytest.mark.parametrize("key", ["archetype", "bottom_line", "do.1", "dont.1", "warning_sign", "follow_up", "decides"])
def test_a_pre_congress_or_pre_role_finding_cannot_support_any_field_but_speaks_and_personality(packet, era, key):
    got = kinds(cite_lf(key), lf_packet(packet, era))
    assert ("evidence_refs." + key, "longform_misuse") in got


@pytest.mark.parametrize("era", ["PRE_CONGRESS_LOCAL_OFFICE_GWINNETT_SCHOOL_BOARD", "PRE_CURRENT_LEADERSHIP_ROLE"])
@pytest.mark.parametrize("key", ["speaks", "personality"])
def test_a_pre_congress_or_pre_role_finding_may_support_speaks_and_personality(packet, era, key):
    assert ("evidence_refs." + key, "longform_misuse") not in kinds(cite_lf(key), lf_packet(packet, era))


@pytest.mark.parametrize("era", ["CURRENT_ROLE_CONDITIONED", "NOT_CURRENT_FORMAL_LEADERSHIP"])
def test_a_current_role_finding_keeps_its_ordinary_permissions(packet, era):
    for key in ("archetype", "do.1", "warning_sign", "decides"):
        assert ("evidence_refs." + key, "longform_misuse") not in kinds(cite_lf(key), lf_packet(packet, era)), key


def test_the_validator_gates_on_the_era_even_when_the_builder_left_the_field_open(packet):
    p = lf_packet(packet, "PRE_CONGRESS_X")
    assert p["longform"][0]["may_support"] == LF_ALL_FIELDS
    issues = V.validate_brief(cite_lf("do.2"), p)[0]
    assert [i["detail"] for i in issues if i["kind"] == "longform_misuse"] and "speaks and personality" in [i for i in issues if i["kind"] == "longform_misuse"][0]["detail"]


def test_a_sentence_citing_an_earlier_career_finding_should_say_so(packet):
    p = lf_packet(packet, "PRE_CONGRESS_LOCAL_OFFICE_X")
    quiet = cite_lf("personality", edit(personality=good_brief()["personality"] + " He answers calmly on the radio."))
    assert [w["field"] for w in V.validate_brief(quiet, p)[1] if w["kind"] == "era_phrase"] == ["personality"]
    speaks = cite_lf("speaks")
    assert [w["field"] for w in V.validate_brief(speaks, p)[1] if w["kind"] == "era_phrase"] == ["speaks"]
    said = cite_lf("personality", edit(personality=good_brief()["personality"] + " Earlier in his career, he answered calmly on the radio."))
    assert not [w for w in V.validate_brief(said, p)[1] if w["kind"] == "era_phrase"]
    current = lf_packet(packet, "CURRENT_ROLE_CONDITIONED")
    assert not [w for w in V.validate_brief(cite_lf("speaks"), current)[1] if w["kind"] == "era_phrase"]


# =========================================================================== pilot 2 review: validator ====
def messages(brief, p, kind, field=None):
    issues, warnings = V.validate_brief(brief, p)
    return [x["detail"] for x in issues + warnings if x["kind"] == kind and (field is None or x["field"] == field)]


def with_items(packet, *items):
    p = copy.deepcopy(packet)
    for iid, band in items:
        p["evidence"] = [i for i in p["evidence"] if i["id"] != iid] + [
            {"id": iid, "band": band, "neutral": False, "direction": "high", "strength": "clear", "plain": "p",
             "may_support": list(LF_ALL_FIELDS)}]
    return p


@pytest.mark.parametrize("phrase", P.SEED_AVOID_PHRASES)
def test_every_seed_line_fails_even_when_the_packet_carries_no_avoid_phrases(packet, phrase):
    for avoid in (None, []):
        p = copy.deepcopy(packet)
        p["avoid_phrases"] = avoid
        b = edit(bottom_line="Walk in with a short, fact-heavy case %s and expect him to test it. A stronger argument earns more than pressure does." % phrase)
        assert ("*", "repetition") in kinds(b, p), (phrase, avoid)
    gone = {k: v for k, v in packet.items() if k != "avoid_phrases"}
    assert ("*", "repetition") in kinds(b, gone)


def test_eight_consecutive_words_from_an_exemplar_fail_whatever_they_carry(packet, tmp_path, monkeypatch):
    example_files(tmp_path, monkeypatch, gold_sentence="Fitz is warm, and he is very visible for a member in his role today.")
    run = " and he is very visible for a member"                                       # eight words, one of them a content word
    assert ("personality", "copied_text") in kinds(edit(personality=good_brief()["personality"] + " He is calm" + run + "."), packet)
    short = " he is very visible for a member"                                          # six words: boilerplate-level, still allowed with one content word
    assert ("personality", "copied_text") not in kinds(edit(personality=good_brief()["personality"] + " Calm," + short + "."), packet)


@pytest.mark.parametrize("sentence,field_ok", [
    ("Few match how reliably he votes.", True), ("Few are less combative in hearings than he is.", True),
    ("Few members hold the floor as firmly as he does.", False),                    # BH.dominance is only above most
    ("Few members work across the aisle as much as he does.", False),               # no BH.coop at all
    ("He rarely uses filler words.", True), ("His answers are rarely long.", False)])  # BH.wpt and IX.wpt_mean are only below most
def test_a_strength_phrase_is_judged_against_the_construct_in_its_own_sentence(packet, sentence, field_ok):
    b = edit(personality=good_brief()["personality"] + " " + sentence)
    assert (("personality", "wrong_strength") not in kinds(b, packet)) is field_ok                # personality cites BH.diligence, VI and HA at the outer bands


@pytest.mark.parametrize("phrase", ["in full", "to the end", "at the very top", "does not fluster"])
def test_the_absolutes_are_banned_at_any_band(packet, phrase):
    b = edit(personality=good_brief()["personality"] + " He sees bills through %s." % phrase)
    assert ("personality", "absolute_claim") in kinds(b, packet)


def test_a_phrase_with_no_construct_word_falls_back_to_the_ids_cited_for_the_field(packet):
    b = edit(personality=good_brief()["personality"] + " He is one of the most careful members.")           # no keyword; the outer-band ids are cited
    assert ("personality", "wrong_strength") not in kinds(b, packet)
    b["evidence_refs"]["personality"] = ["TR.C", "TR.A"]
    assert ("personality", "wrong_strength") in kinds(b, packet)


WARN = "He is usually quick to answer your case. If he stops and keeps restating his own view, the room has turned into a contest. Hold back new points and offer one fact."


def warn(change, packet, refs=("DF", "MA")):
    b = edit(warning_sign=WARN.replace("If he stops and keeps restating his own view", change))
    b["evidence_refs"]["warning_sign"] = list(refs)
    return messages(b, packet, "warning_form")


def test_short_replies_are_never_the_change_for_a_member_high_on_burst(packet):
    p = with_items(packet, ("IX.burst", ABOVE))
    assert any("IX.burst" in m for m in warn("If his replies turn short and clipped", p))
    for word in ("shorter", "terse", "brief"):
        assert any("IX.burst" in m for m in warn("If his replies get %s" % word, p)), word
    assert not warn("If his replies turn short and clipped", with_items(packet, ("IX.burst", MID)))
    assert not warn("If his replies turn short and clipped", with_items(packet, ("IX.burst", BELOW)))


def test_long_answers_are_never_the_change_for_a_member_high_on_p90(packet):
    p = with_items(packet, ("IX.wpt_p90", WELL_ABOVE))
    for word in ("long", "longer", "stretch", "stretches", "stretching", "stretched"):
        assert any("IX.wpt_p90" in m for m in warn("If his replies %s on" % word, p)), word
    assert any("IX.wpt_p90" in m for m in warn("If his replies get long", with_items(packet, ("IX.wpt_p90", ABOVE))))        # above most is enough
    assert not warn("If his replies get long", with_items(packet, ("IX.wpt_p90", MID))) and not warn("If his replies get long", packet)


def test_answer_length_words_are_never_the_change_for_a_member_high_on_gini(packet):
    p = with_items(packet, ("IX.gini", ABOVE))
    assert any("IX.gini" in m for m in warn("If his replies run long", p)) and any("IX.gini" in m for m in warn("If his replies turn short", p))
    assert not warn("If his replies run long", with_items(packet, ("IX.gini", MID)))


def test_the_cue_must_not_rest_only_on_answer_length_when_a_stronger_cue_exists(packet):
    assert warn("If he stops", packet, refs=("IX.wpt_mean", "MA")) and "answer-length" in warn("If he stops", packet, refs=("IX.wpt_mean", "MA"))[0]
    assert not warn("If he stops", packet, refs=("IX.wpt_mean", "DF"))                       # a non-length cue is cited as well
    only_length = with_items(packet, ("IX.discmark", MID), ("HA", MID), ("IX.defense", ABOVE), ("DF", ABOVE))
    only_length["gates"] = {**only_length["gates"], "DF": False}
    assert not warn("If he stops", only_length, refs=("IX.wpt_mean", "MA"))                  # no cue-eligible item at +3 or -3 other than length: nothing to prefer


def test_a_warning_that_tells_the_reader_to_stop_what_a_do_asks_for_is_flagged_for_the_reviewer(packet):
    b = edit(warning_sign=WARN.replace("Hold back new points and offer one fact.", "Stop pressing and offer one fact."))
    b["do"][0] = "Press your case with one checkable source"
    assert [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "warning_conflict"]
    b["do"][0] = "Name the decision you need up front"
    assert not [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "warning_conflict"]
    keep = edit(warning_sign=WARN.replace("Hold back new points and offer one fact.", "Keep pushing for a same-day answer."))
    keep["dont"][0] = "Push for a same-day answer"
    assert [w for w in V.validate_brief(keep, packet)[1] if w["kind"] == "warning_conflict"]


def test_personality_may_cite_at_most_two_biography_or_sensibility_items(packet):
    b = good_brief()
    b["evidence_refs"]["personality"] = ["TR.A", "BIO.state", "BIO.committees"]
    assert ("evidence_refs.personality", "bio_cap") not in kinds(b, packet)
    b["evidence_refs"]["personality"] = ["TR.A", "BIO.state", "BIO.committees", "PS.1"]
    assert ("evidence_refs.personality", "bio_cap") in kinds(b, packet)
    b["evidence_refs"]["personality"] = ["TR.A", "LD", "BIO.state", "PS.1"]                 # LD is not counted
    assert ("evidence_refs.personality", "bio_cap") not in kinds(b, packet)
    other = good_brief()
    other["evidence_refs"]["speaks"] = ["BH.wpt", "BIO.state", "BIO.committees", "PS.1"]       # the cap is for personality only
    assert ("evidence_refs.speaks", "bio_cap") not in kinds(other, packet)


@pytest.mark.parametrize("wording", ["Outside pressure gets to him more than most.", "Outside pressure lands harder on him than on most.", "Outside pressure unsettles him."])
def test_rc_is_never_worded_as_stress(packet, wording):
    p = with_items(packet)
    b = edit(decides=good_brief()["decides"] + " " + wording)
    assert ("decides", "construct_conflation") in kinds(b, p)                                    # decides cites RC
    plain = edit(personality=good_brief()["personality"] + " " + wording)
    assert ("personality", "construct_conflation") in kinds(plain, p)                            # personality cites no TR.N and no RC: still wrong
    plain["evidence_refs"]["personality"] = plain["evidence_refs"]["personality"] + ["TR.N"]
    assert ("personality", "construct_conflation") not in kinds(plain, p)                       # TR.N words are TR.N words
    named = edit(decides=good_brief()["decides"] + " Outside pressure moves him less than most members.")
    assert ("decides", "construct_conflation") not in kinds(named, p)


@pytest.mark.parametrize("sentence", ["His tone in hearings is mild, so expect a calm exchange.", "Her language is sharp, so the talk stays blunt.",
                                      "His words are gentle, so the conversation runs smoothly.", "His tone is mild, so the meeting stays quiet.",
                                      "His language is plain, so expect a quick and quiet exchange.", "His tone is mild, so the exchange stays civil.",
                                      "Her words are sharp, and you can expect a brisk talk."])
def test_a_forecast_of_the_conversation_after_a_words_or_hearing_fact_is_flagged(packet, sentence):
    b = edit(speaks=good_brief()["speaks"] + " " + sentence)
    assert [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "conversation_forecast" and w["field"] == "speaks"]


def test_a_forecast_without_a_words_or_hearing_fact_is_not_flagged(packet):
    for sentence in ("Bring one source, so expect a quick exchange.", "Hearings bring out a mild side.", "His tone is mild, so keep the ask short."):
        b = edit(speaks=good_brief()["speaks"] + " " + sentence)
        assert not [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "conversation_forecast"], sentence


def test_a_dont_that_cites_a_dos_id_or_shares_its_noun_or_contradicts_decides_is_flagged(packet):
    base = good_brief()
    assert not [w for w in V.validate_brief(base, with_items(packet))[1] if w["kind"] == "mirrored_dont" and "decides" in w["detail"]]
    b = good_brief()
    b["do"][1] = "Let him hold the floor when he takes it"
    b["dont"][0] = "Compete with him for the floor"
    b["evidence_refs"]["do.2"], b["evidence_refs"]["dont.1"] = ["BH.dominance"], ["RC"]
    got = [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "mirrored_dont" and w["field"] == "dont.1"]
    assert any("floor" in w["detail"] for w in got)
    ids = good_brief()
    ids["evidence_refs"]["do.3"], ids["evidence_refs"]["dont.3"] = ["IX.discmark", "BH.wpt"], ["IX.discmark", "HA"]
    assert [w for w in V.validate_brief(ids, packet)[1] if w["kind"] == "mirrored_dont" and w["field"] == "dont.3" and "IX.discmark" in w["detail"]]
    proc = good_brief()
    proc["evidence_refs"]["do.3"], proc["evidence_refs"]["dont.3"] = ["PROC"], ["PROC"]
    proc["dont"][2] = "Assume his quick tone means agreement"
    assert not [w for w in V.validate_brief(proc, packet)[1] if w["kind"] == "mirrored_dont" and w["field"] == "dont.3" and "PROC" in w["detail"]]


def test_a_dont_about_repeating_the_ask_needs_rc_less(packet):
    more = copy.deepcopy(packet)
    for i in more["evidence"]:
        if i["id"] == "RC":
            i["direction"] = "more"
    b = good_brief()
    b["dont"][0] = "Repeat the ask louder when he pushes back"
    assert [w for w in V.validate_brief(b, more)[1] if w["kind"] == "mirrored_dont" and w["field"] == "dont.1" and "RC" in w["detail"]]
    assert not [w for w in V.validate_brief(b, packet)[1] if w["kind"] == "mirrored_dont" and w["field"] == "dont.1" and "RC" in w["detail"]]
    decides = edit(decides=good_brief()["decides"] + " A firm request, repeated, can help.")
    decides["dont"][0] = "Repeat the ask louder when he pushes back"
    assert [w for w in V.validate_brief(decides, packet)[1] if w["kind"] == "mirrored_dont" and "decides" in w["detail"]]
    negated = edit(decides=good_brief()["decides"] + " Do not repeat the ask.")
    negated["dont"][0] = "Repeat the ask louder when he pushes back"
    assert not [w for w in V.validate_brief(negated, packet)[1] if w["kind"] == "mirrored_dont" and "decides" in w["detail"]]


def exemplar_corpus(tmp_path, monkeypatch, gold=(), inline=()):
    (tmp_path / "exemplars.json").write_text(json.dumps({"exemplars": list(gold)}), encoding="utf-8")
    (tmp_path / "writer_prompt.md").write_text("# prompt\n\n" + "".join("```\n%s\n```\n\n" % json.dumps(r) for r in inline), encoding="utf-8")
    monkeypatch.setattr(P, "EXEMPLARS_PATH", tmp_path / "exemplars.json")
    monkeypatch.setattr(V, "PROMPT_PATH", tmp_path / "writer_prompt.md")
    for cached in (V.example_grams, V.example_texts, V.example_rows):
        cached.cache_clear()


def exemplar_row(name, animal, do, dont, warning="", personality=None):
    return {"display_name": name, "archetype": {"animal": animal, "descriptor": "d", "justification": "j"}, "do": do, "dont": dont,
            "warning_sign": warning, "personality": personality or "%s is a fixed point." % name.split()[-1], "bottom_line": "b", "follow_up": "f"}


def tracking(brief, packet):
    return [w for w in V.validate_brief(brief, packet)[1] if w["kind"] == "exemplar_tracking"]


def test_three_bullets_that_track_one_exemplar_are_flagged_two_are_not(packet, tmp_path, monkeypatch):
    gold = good_brief()
    row = lambda do, dont: exemplar_row("Pat Gold", "Owl", do, dont)
    ex = row(gold["do"][:3] + ["Walk through your slides"], ["Bring a crowd", "Hide the ask", "Skip the memo"])
    exemplar_corpus(tmp_path, monkeypatch, gold=[ex])
    assert tracking(good_brief(), packet)                                                           # three Do bullets match: 3 reaches the bar
    two = row(gold["do"][:2] + ["Walk through your slides", "Bring a crowd"], ["Hide the ask", "Skip the memo", "Wave the flag"])
    exemplar_corpus(tmp_path, monkeypatch, gold=[two])
    assert not tracking(good_brief(), packet)                                                       # two matches: below the bar
    mixed = row(gold["do"][:2], [gold["dont"][0]])
    exemplar_corpus(tmp_path, monkeypatch, gold=[mixed])
    assert tracking(good_brief(), packet)                                                           # Do and Don't bullets count together


def test_bullets_tracking_two_different_exemplars_do_not_add_up(packet, tmp_path, monkeypatch):
    gold = good_brief()
    a = exemplar_row("Pat Gold", "Owl", gold["do"][:2] + ["Walk through your slides", "Bring a crowd"], ["Hide the ask", "Skip the memo", "Wave the flag"])
    b = exemplar_row("Sam Gold", "Owl", ["Open with a joke", "Bring a crowd", "Skip the slides", "Take notes"], [gold["dont"][0], "Skip the memo", "Wave the flag"])
    exemplar_corpus(tmp_path, monkeypatch, gold=[a, b])
    assert not tracking(good_brief(), packet)


def test_the_bar_is_two_when_the_member_shares_the_exemplars_animal_and_a_warning_sign_match_counts_one(packet, tmp_path, monkeypatch):
    gold = good_brief()
    owl_two = exemplar_row("Pat Gold", "Bull", gold["do"][:2] + ["Walk through your slides", "Bring a crowd"], ["Hide the ask", "Skip the memo", "Wave the flag"])
    exemplar_corpus(tmp_path, monkeypatch, gold=[owl_two])
    assert packet["suggested_animal"] == "Bull" and tracking(good_brief(), packet)                  # same animal: two is enough
    owl_two["archetype"]["animal"] = "Owl"
    warning = good_brief()["warning_sign"]
    both = exemplar_row("Pat Gold", "Owl", gold["do"][:2] + ["Walk through your slides", "Bring a crowd"], ["Hide the ask", "Skip the memo", "Wave the flag"], warning=warning)
    exemplar_corpus(tmp_path, monkeypatch, gold=[both])
    assert tracking(good_brief(), packet)                                                           # two bullets plus a five-word warning match is three


def test_a_members_own_exemplar_and_the_inline_prompt_examples_follow_the_same_rules(packet, tmp_path, monkeypatch):
    gold = good_brief()
    own = exemplar_row("Rich Gold", "Bull", gold["do"][:3], [gold["dont"][0]], personality="Rich keeps a tight grip on timing.")
    exemplar_corpus(tmp_path, monkeypatch, gold=[own])
    assert not tracking(good_brief(), packet)                                                       # the packet's member is "Rich": this is their own gold
    inline = exemplar_row("Quill Example", "Tortoise", gold["do"][:3], [gold["dont"][0]], personality="Quill reads every line.")
    exemplar_corpus(tmp_path, monkeypatch, inline=[inline])
    assert tracking(good_brief(), packet)


def test_pressure_words_follow_the_rc_strength_not_a_band(packet):
    clear = edit(decides=good_brief()["decides"] + " Pressure counts for little with him.")
    assert ("decides", "wrong_strength") in kinds(clear, packet)                                    # RC is clear here: no low-strength words
    outer = copy.deepcopy(packet)
    next(i for i in outer["evidence"] if i["id"] == "RC")["strength"] = "outer"
    assert ("decides", "wrong_strength") not in kinds(clear, outer)
    more = copy.deepcopy(outer)
    next(i for i in more["evidence"] if i["id"] == "RC")["direction"] = "more"
    assert ("decides", "wrong_strength") in kinds(clear, more)                                      # outer, but the wrong way for "little"
