from fec.surfaces.staffer_identity_resolution import detect_candidates, name_key
import json
import pytest
from fec.surfaces import staffer_identity_resolution as resolution
from fec.surfaces.staffer_identity_auto import resolve_identities


def test_same_name_is_candidate_never_automatic_alias():
    records = [{"id": "a", "label": "Kathleen Coulombe"}, {"id": "b", "label": "KATHLEEN COULOMBE"}]
    groups = detect_candidates(records)
    assert len(groups) == 1
    assert groups[0]["status"] == "CONFIRMATION_REQUIRED"
    assert not groups[0]["auto_merge"]
    assert detect_candidates(records, {"b": "a"}) == []


def test_title_and_known_nickname_retrieve_greg_without_merge():
    records = [{"id": str(i), "label": label} for i, label in enumerate(["Greg Mesack", "GREGORY MESACK", "mr GREGORY MESACK"])]
    groups = detect_candidates(records)
    assert len(groups) == 1 and len(groups[0]["node_ids"]) == 3
    assert not groups[0]["auto_merge"]
    assert name_key("Greg Mesack Jr") != name_key("Greg Mesack Sr")


def test_detailed_history_and_shared_firm_rank_but_do_not_prove_identity():
    history = "Legislative director to Representative Example from 2019 through 2021"
    records = [{"id": i, "label": "Jamie Example", "covered_positions": [history]} for i in ["a", "b"]]
    episodes = [{"person_id": i, "employer_id": "same firm"} for i in ["a", "b"]]
    group = detect_candidates(records, episodes=episodes)[0]
    assert group["priority"] == "corroborated_candidate"
    assert "EXACT_COVERED_POSITION_TEXT" in group["evidence_types"]
    assert "SHARED_FILING_REGISTRANT" in group["evidence_types"]
    assert not group["auto_merge"]


def test_persistent_merge_extends_existing_group_without_cycles(tmp_path, monkeypatch):
    path = tmp_path / "assertions.json"
    path.write_text(json.dumps({"identity_groups": [{"primary_id": "person:a", "alias_ids": ["person:b"],
        "display_name": "Kathleen Coulombe", "status": "USER_CONFIRMED_SAME_PERSON"}]}))
    monkeypatch.setattr(resolution, "USER_ASSERTIONS", path)
    result = resolution.record_identity_decision("person:b", ["person:c"])
    assert result["group"]["primary_id"] == "person:a"
    assert result["identity_aliases"] == {"person:b": "person:a", "person:c": "person:a"}
    assert result["group"]["display_name"] == "Kathleen Coulombe"
    assert len(json.loads(path.read_text())["identity_groups"]) == 1
    with pytest.raises(ValueError):
        resolution.record_identity_decision("person:a", ["person:c"], "keep_separate")


def test_keep_separate_persists_and_filters_only_denied_pairs(tmp_path, monkeypatch):
    path = tmp_path / "assertions.json"
    path.write_text('{"identity_groups": []}')
    monkeypatch.setattr(resolution, "USER_ASSERTIONS", path)
    resolution.record_identity_decision("person:a", ["person:b"], "keep_separate")
    data = json.loads(path.read_text())
    groups = detect_candidates([{"id": "person:" + i, "label": "Same Name"} for i in "abc"],
                               keep_separate_pairs=data["keep_separate_pairs"])
    assert groups[0]["candidate_pairs"] == [["person:a", "person:c"], ["person:b", "person:c"]]
    with pytest.raises(ValueError):
        resolution.record_identity_decision("person:a", ["person:b"])


def test_invalid_decision_does_not_modify_assertions(tmp_path, monkeypatch):
    path = tmp_path / "assertions.json"
    path.write_text('{"identity_groups": []}')
    monkeypatch.setattr(resolution, "USER_ASSERTIONS", path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        resolution.record_identity_decision("../../bad", ["person:okay"])
    assert path.read_bytes() == original


def test_keep_separate_follows_reprimary_and_multihop_aliases():
    records = [{"id": i, "label": "Same Name"} for i in ["a", "b", "c", "d"]]
    # A was separated from B, then A -> C -> D was confirmed.
    groups = detect_candidates(records, {"a": "c", "c": "d"}, keep_separate_pairs=[["a", "b"]])
    assert groups == []
    # Obsolete self-separation no longer suppresses unrelated B/D candidates.
    groups = detect_candidates(records, {"a": "c", "c": "d"}, keep_separate_pairs=[["a", "d"]])
    assert groups[0]["node_ids"] == ["b", "d"]
    assert groups[0]["candidate_pairs"] == [["b", "d"]]


def test_alias_cycle_fails_explicitly():
    with pytest.raises(ValueError, match="Cycle"):
        detect_candidates([{"id": "a", "label": "Same Name"}], {"a": "b", "b": "a"})


def test_middle_initial_variants_are_retrieved_for_review_but_never_merge():
    records = [{"id": "politico:k", "label": "Kelvin Lassiter"}, {"id": "person:hill:k", "label": "Kelvin T Lassiter"}]
    groups = detect_candidates(records)
    assert len(groups) == 1
    assert groups[0]["node_ids"] == ["person:hill:k", "politico:k"]
    assert groups[0]["candidate_pairs"] == [["person:hill:k", "politico:k"]]
    assert groups[0]["status"] == "CONFIRMATION_REQUIRED" and not groups[0]["auto_merge"]
    assert groups[0]["priority"] == "name_only_candidate"
    assert [r["node_id"] for r in groups[0]["records"]] == ["politico:k", "person:hill:k"]
    # Retrieval widened only: the evidence resolver leaves the same pair unresolved.
    nodes = {r["id"]: {**r, "kind": "person", "provenance": {"path": "/src", "sha256": "x"}} for r in records}
    resolved = resolve_identities(nodes, [], [], {}, [])
    assert resolved["identity_groups"] == [] and resolved["unresolved_candidates"][0]["auto_merge"] is False
    # compatible() still decides: conflicting middle names or suffixes are not candidates.
    assert detect_candidates([{"id": "a", "label": "Kelvin T Lassiter"}, {"id": "b", "label": "Kelvin R Lassiter"}]) == []
    assert detect_candidates([{"id": "a", "label": "Greg Mesack Jr"}, {"id": "b", "label": "Greg Mesack Sr"}]) == []


def test_family_retrieval_keeps_only_compatible_pairs_and_members():
    records = [{"id": str(i), "label": label} for i, label in enumerate(["Greg Mesack", "Greg J Mesack", "Gregory Robert Mesack"])]
    group = detect_candidates(records)[0]
    assert group["node_ids"] == ["0", "1", "2"]
    assert group["candidate_pairs"] == [["0", "1"], ["0", "2"]]
    records = [{"id": str(i), "label": label} for i, label in enumerate(["Greg J Mesack", "Gregory Robert Mesack", "Gregory R Mesack"])]
    group = detect_candidates(records)[0]
    assert group["node_ids"] == ["1", "2"]
    assert [r["node_id"] for r in group["records"]] == ["1", "2"]
    assert group["candidate_pairs"] == [["1", "2"]]


def test_automatic_inferred_merges_counts_applied_automatic_groups(tmp_path, monkeypatch):
    path = tmp_path / "assertions.json"
    path.write_text(json.dumps({"identity_groups": [{"primary_id": "person:c", "alias_ids": ["person:d"],
        "display_name": "Kathleen Coulombe", "status": "USER_CONFIRMED_SAME_PERSON"}]}))
    monkeypatch.setattr(resolution, "USER_ASSERTIONS", path)
    monkeypatch.setattr(resolution, "LDA_ROOT", tmp_path / "no-lda-corpus")
    graph = {"nodes": {
        "person:a": {"kind": "person", "label": "Laura Cylke", "identity_status": "REFERENCE_RESOLVED_SAME_PERSON",
                     "identity_alias_ids": ["person:b"]},
        "person:c": {"kind": "person", "label": "Kathleen Coulombe", "identity_status": "USER_CONFIRMED_SAME_PERSON",
                     "identity_alias_ids": ["person:d"]},
        "person:e": {"kind": "person", "label": "Plain Person"},
        "office:1": {"kind": "container", "label": "Office", "identity_status": "REFERENCE_RESOLVED_SAME_PERSON"}}}
    result = resolution.load_identity_resolution(tmp_path, graph)
    assert result["counts"]["automatic_inferred_merges"] == 1
    assert result["counts"]["automatic_inferred_aliases"] == 1
    assert result["counts"]["user_confirmed_aliases"] == 1
    assert "Only explicit user-confirmed aliases are applied automatically" not in resolution.__doc__
