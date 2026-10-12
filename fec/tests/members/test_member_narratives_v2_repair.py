import importlib.util
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_module("narrative_ledger", "fec/members/build_member_narrative_ledger.py")
revision = load_module("narrative_revision", "fec/members/revise_member_narratives_v2.py")
descriptive = load_module("descriptive_validation", "fec/members/validate_descriptive_profiles.py")


def assert_descriptive_contract(artifact):
    artifact_path = ROOT / "member_narratives_v2.json"
    basis_path = ROOT / "data/members/profile_basis_v2.json"
    evidence_path = ROOT / "data/members/member_narrative_evidence_v4.json"
    receipt = json.loads((ROOT / "data/members/member_narratives_v3_descriptive_receipt.json").read_text(encoding="utf-8"))
    basis = json.loads(basis_path.read_text(encoding="utf-8"))
    errors = descriptive.validate_descriptive_profiles(
        artifact, receipt,
        basis_sha256=hashlib.sha256(basis_path.read_bytes()).hexdigest(),
        evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        active_artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        member_names=basis["members"],
    )
    assert errors == []
    assert receipt["semantic_model_review"] is False
    assert receipt["independent_human_audio_review"] is False
    for row in artifact["members"].values():
        assert 200 <= descriptive.word_count(row) <= 250
        assert "meeting_playbook" not in row
        assert row["recurring_audio_pattern_claimed"] is False


def test_desk_rank_matches_bisect_left_over_n_minus_one():
    rank = builder.desk_rank([1.0, 2.0, 2.0, 4.0])
    assert rank.n == 4
    assert rank(1.0) == 0.0
    assert rank(2.0) == round(1 / 3, 3)
    assert rank(4.0) == 1.0


def test_known_repair_partition_and_missing_measure_gate():
    # The 268 / 143 / 125 partition describes the PRE-repair artifact (the 2026-08-31 briefs). After the
    # 2026-09-01 semantic repair was activated, the installed artifact must show the repaired invariant
    # instead: every brief that talks about cross-party behaviour has the share-based measure present.
    # The pre-repair shape is still pinned, against the backup the activation wrote, so the record of
    # what needed repair does not silently drift.
    artifact = json.loads((ROOT / "member_narratives_v2.json").read_text(encoding="utf-8"))
    evidence = json.loads((ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8"))["members"]
    if artifact.get("schema") == descriptive.SCHEMA:
        # Descriptive profiles may acknowledge a missing cross-party comparison.
        # That acknowledgement is not an unsupported directional claim.
        assert_descriptive_contract(artifact)
        for name, row in artifact["members"].items():
            if revision.coop_band(evidence[name]) is None:
                assert "The share-based measure of cross-party legislative connections is" not in row["p1"]
    else:
        targets = revision.known_cross_party_targets(artifact)
        assert targets, "installed artifact mentions cross-party behaviour nowhere"
        assert sum(revision.coop_band(evidence[name]) is None for name in targets) == 0, (
            "installed artifact talks about cross-party behaviour for members whose measure is absent"
        )

    backup_path = ROOT / "member_narratives_v2.pre_semantic_repair_20260901.json"
    if backup_path.exists():
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        pre_targets = revision.known_cross_party_targets(backup)
        assert len(pre_targets) == 268
        assert sum(revision.coop_band(evidence[name]) is not None for name in pre_targets) == 143
        assert sum(revision.coop_band(evidence[name]) is None for name in pre_targets) == 125

    uncovered = next(name for name in sorted(evidence) if revision.coop_band(evidence[name]) is None)
    draft = (
        "**Overall personality.** They are direct.\n"
        "**Manner of speech & landmines.** They keep answers brief.\n"
        "**Persuasive approach.** They work across the aisle."
    )
    issues = revision.mechanical_issues(draft, uncovered, evidence[uncovered])
    assert "cross-party subject appears although the measure is absent" in issues


def test_evidence_ledger_construct_and_speech_contracts():
    document = json.loads((ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8"))
    assert document["schema"] == "member-narrative-evidence-v4"
    assert document["counts"]["members"] == 533
    assert document["counts"]["cross_party_covered"] == 300  # 301 -> 300: CU_IDENTITY_FIX (0b65a2cab) drops Menendez
    assert document["counts"]["speech_pass"] == 533
    assert document["method"]["constructs_independent"] is True
    for row in document["members"].values():
        profile = row.get("speech_profile") or {}
        assert profile.get("status") == "PASS"
        # Two complementary audio layers since 2026-09-07.  Both are checked here:
        # channel-key coverage of the voiceprint layer used to ride on this file's
        # `standardized_channels` fallback, which now never fires because the
        # voiceprint lives under its own key.  A layer is a dict or None, never a
        # truthy-but-empty object -- an empty object is what makes a renderer show
        # a member with verified audio as having none.
        for acoustic in (profile.get("general_audio_habits"),
                         profile.get("verified_voiceprint_baseline")):
            assert acoustic is None or isinstance(acoustic, dict)
            assert acoustic != {}
            if not acoustic:
                continue
            channels = acoustic.get("channels") or acoustic.get("standardized_channels") or {}
            assert set(channels) <= {"pause", "pace", "volume_dyn", "pitch_var", "strain", "f0_mean"}


def test_prompts_pin_construct_separation_and_fail_closed_review():
    writer = (ROOT / "fec/members/member_narratives_v2_prompt.md").read_text(encoding="utf-8")
    reviewer = (ROOT / "fec/members/member_narratives_v2_review_prompt.md").read_text(encoding="utf-8")
    for phrase in (
        "independently true", "Broad Agreeableness", "cross-party cooperation",
        "Broad Neuroticism", "legislative diligence", "Visibility",
    ):
        assert phrase in writer
    assert "fail-closed" in reviewer
    assert "construct_conflation" in reviewer
    assert "one-on-one meeting book" in writer
    assert "what exactly should i say, bring, and do" in writer.casefold()
    assert "abstract_without_meeting_translation" in reviewer
    assert "practical_playbook" in reviewer
    assert "identity/acoustic window is not a conversational turn" in writer
    assert "minutes, or many minutes" in writer
    assert "fixed_window_turn_conflation" in reviewer
    assert "before the member assumed the current leadership position" in writer
    assert "leadership_source_era_conflation" in reviewer


def test_private_state_defense_and_trigger_gates_are_fail_closed():
    evidence = json.loads((ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8"))["members"]
    name, record = next(
        (name, row) for name, row in evidence.items()
        if not (row.get("speech_profile") or {}).get("repeated_defensive_reactions")
        and not ((row.get("speech_profile") or {}).get("supported_topic_associations") or [])
    )
    base = (
        "**Overall personality.** Broad Neuroticism is below most.\n"
        "**Manner of speech & landmines.** There is no defensive or trigger topic to flag.\n"
        "**Persuasive approach.** Use a specific request."
    )
    issues = revision.mechanical_issues(base, name, record)
    assert "defensive language appears without the repeated-defensive evidence gate" in issues
    assert "topic/trigger language appears without a supported topic association" in issues

    synonym_draft = base.replace(
        "There is no defensive or trigger topic to flag",
        "There is no pushback or pressure point to flag",
    )
    synonym_issues = revision.mechanical_issues(synonym_draft, name, record)
    assert "defensive language appears without the repeated-defensive evidence gate" in synonym_issues
    assert "topic/trigger language appears without a supported topic association" in synonym_issues

    private_state = base.replace("Broad Neuroticism is below most", "They are calm and emotionally stable")
    private_issues = revision.mechanical_issues(private_state, name, record)
    assert any(issue.startswith("unsupported private-state term:") for issue in private_issues)


def test_plain_narrative_trait_phrasing_is_no_longer_flagged():
    # The broad-trait canonical-sentence rule was removed from mechanical_issues on 2026-09-01: Paul ruled
    # the briefs must read as holistic narrative with flat trait statements in plain words, not literal
    # band labels or the old rigid "Broad X is <band> in the expert-perceived reading" sentence. Direction
    # and strength of trait claims are the semantic reviewer's job again, as they were before that date.
    evidence = json.loads((ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8"))["members"]
    name, record = next(iter(evidence.items()))
    draft = (
        "**Overall personality.** They are outgoing and curious, more so than most of the chamber.\n"
        "**Manner of speech & landmines.** They answer questions directly and keep replies brief.\n"
        "**Persuasive approach.** Use a concrete, well-defined request."
    )
    assert revision.mechanical_issues(draft, name, record) == []


def test_targeted_longform_interviews_are_display_only_and_receipted():
    artifact_path = ROOT / "data/speech/member_interview_descriptive_v1.json"
    receipt_path = ROOT / "data/speech/member_interview_descriptive_v1.receipt.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert artifact["status"] == "DISPLAY_ONLY_NO_ENGINE_WIRING"
    assert artifact["contract"]["big_five_or_rank_changes_allowed"] is False
    assert artifact["contract"]["stance_allocation_or_persuasion_changes_allowed"] is False
    assert set(artifact["members"]) == {"Steve Scalise", "Maxine Waters", "Alex Padilla"}
    assert receipt["counts"]["catalogued_sources"] == 6
    assert receipt["counts"]["verified_sources"] == 6
    assert receipt["counts"]["accepted_exact_windows"] == 18
    for row in artifact["members"].values():
        assert row["sources"] == 2
        assert row["clips"] == 6
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()


def test_candidate_only_longform_is_removed_from_active_profile_prose():
    artifact = json.loads((ROOT / "member_narratives_v2.json").read_text(encoding="utf-8"))
    if artifact.get("schema") == descriptive.SCHEMA:
        assert_descriptive_contract(artifact)
        for name in ("Steve Scalise", "Mike Johnson", "Maxine Waters", "Alex Padilla"):
            row = artifact["members"][name]
            assert row["sources"]
            assert row["recurring_audio_pattern_claimed"] is False
        steve = artifact["members"]["Steve Scalise"]
        reports = [source for source in steve["sources"]
                   if source.get("type") == "uncorroborated_secondhand_report"]
        assert len(reports) == 1
        assert reports[0]["audio_confirmed"] is False
        assert reports[0]["frequency"] is None
        assert "secondhand" in steve["p3"].casefold()
        return
    pre_longform = json.loads((
        ROOT / "data/members/member_narratives_v2_work/member_narratives_v2.pre_basis_restamp_20260903T023548Z.json"
    ).read_text(encoding="utf-8"))
    for name in ("Steve Scalise", "Mike Johnson", "Maxine Waters", "Alex Padilla"):
        row = artifact["members"][name]
        expected = pre_longform["members"][name]
        assert all(row[key] == expected[key] for key in ("p1", "p2", "p3"))
        assert row["meeting_playbook"]["observed_longform"] is None
        assert "complete reviewed answer" not in " ".join(row[key] for key in ("p1", "p2", "p3")).casefold()


def test_every_member_uses_the_active_profile_contract():
    artifact = json.loads((ROOT / "member_narratives_v2.json").read_text(encoding="utf-8"))
    if artifact.get("schema") == descriptive.SCHEMA:
        assert_descriptive_contract(artifact)
        return
    receipt = json.loads((ROOT / "data/members/member_narratives_v2_semantic_receipt.json").read_text(
        encoding="utf-8"))
    assert len(artifact["members"]) == 533
    assert receipt["counts"]["meeting_playbooks"] == 533
    for row in artifact["members"].values():
        playbook = row["meeting_playbook"]
        assert set(("open", "bring", "run_the_room", "close", "derivation", "scope")) <= set(playbook)
        assert "[action]" in playbook["open"]
        assert "one page" in playbook["bring"].casefold()
        assert playbook["run_the_room"].strip()
        assert "owner" in playbook["close"].casefold()
        assert "date" in playbook["close"].casefold()
    maxine = artifact["members"]["Maxine Waters"]["meeting_playbook"]
    for name in ("Steve Scalise", "Mike Johnson", "Maxine Waters", "Alex Padilla"):
        playbook = artifact["members"][name]["meeting_playbook"]
        assert playbook["observed_longform"] is None
        assert "reviewed_longform" not in json.dumps(playbook["derivation"])
    # RE-PINNED 2026-10-11: the post-refresh evidence ledger moves Waters to above-typical pressure return.
    assert maxine["derivation"]["close"] == "above_typical_pressure_return"
    assert maxine["close"].casefold().startswith("a firm")  # the above-typical close wording


def test_member_desk_wires_and_labels_the_meeting_playbook():
    builder = (ROOT / "fec/surfaces/make_member_desk.py").read_text(encoding="utf-8")
    template = (ROOT / "fec/surfaces/templates/make_member_desk_00.tpl").read_text(encoding="utf-8")
    assert "'meeting_playbook': nar.get('meeting_playbook')" in builder
    for label in (
        "In the room", "Open:", "Bring:", "Run the room:", "Close:",
        "Working read — public evidence, not a private prediction", "What you may hear",
        "Why this approach", "Reviewed long-form example", "Meeting use:",
    ):
        assert label in template
    assert template.index("In the room") < template.index("Working read — public evidence")
