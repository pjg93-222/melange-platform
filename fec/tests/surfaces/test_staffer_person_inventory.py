import gzip
import hashlib
import json

from fec.surfaces import staffer_person_inventory as inventory


def source_fixture(tmp_path, monkeypatch):
    assertions = tmp_path / "user_assertions.json"
    assertions.write_text('{"identity_groups": []}')
    monkeypatch.setattr(inventory, "USER_ASSERTIONS", assertions)
    lda = tmp_path / "lda"
    lda.mkdir()
    monkeypatch.setattr(inventory, "LDA_ROOT", lda)
    path = lda / "people.gz"
    with gzip.open(path, "wt") as stream:
        for source_id in ["lda:lobbyist:154321", "lda:lobbyist:68016"]:
            stream.write(json.dumps({"source_person_id": source_id, "display_name": "KATHLEEN COULOMBE"}) + "\n")
    (lda / "lda_source_native_corpus_receipt.json").write_text(json.dumps({"status": "PASS", "files": [{
        "path": path.name, "role": "lobbyist_assertions", "records": 2, "sha256": inventory.sha(path)}]}))
    raw = tmp_path / "audits/staffer_network/politico_profile_crawl_20260903_raw"
    raw.mkdir(parents=True)
    directory = tmp_path / "directory.ndjson"
    directory.write_text(json.dumps({"profile_uuid": "unfetched", "name": "Unfetched Staffer"}) + "\n")
    (raw / "crawl_verification.json").write_text(json.dumps({"integrity_status": "PASS",
        "counts": {"sealed_directory_unique_profiles": 1}, "inputs": {"directory": str(directory)},
        "input_sha256": {"directory": inventory.sha(directory)}}))
    # The current inventory also binds a verified operational population.
    runtime = tmp_path / "audits/staffer_live_runtime"
    runtime.mkdir(parents=True)
    receipt = runtime / "spine_receipt.json"
    verification = runtime / "spine_verification.json"
    receipt.write_text('{}')
    verification.write_text('{"status":"PASS"}')
    (runtime / "operational_population.json").write_text(json.dumps({
        "status": "PASS", "schema": "melange-operational-staffer-population-v1",
        "staffer_ids": [], "staffer_count": 0,
        "spine": {"receipt": str(receipt), "receipt_sha256": inventory.sha(receipt),
                  "verification": str(verification), "verification_sha256": inventory.sha(verification)}}))
    return directory


def test_same_name_ids_stay_separate_and_unfetched_profile_present(tmp_path, monkeypatch):
    source_fixture(tmp_path, monkeypatch)
    result = inventory.load_inventory(tmp_path, {"nodes": {}})
    assert "person:lda:lobbyist:154321" in result["nodes"]
    assert "person:lda:lobbyist:68016" in result["nodes"]
    assert result["nodes"]["politico:unfetched"]["profile_pending"]
    assert result["counts"]["lda_source_person_ids"] == 2
    assert result["counts"]["politico_directory_source_ids"] == 1


def test_only_unique_existing_spine_id_joins(tmp_path, monkeypatch):
    source_fixture(tmp_path, monkeypatch)
    graph = {"nodes": {"person:hill:person:one": {"lda_source_person_id": "lda:lobbyist:154321"}}}
    result = inventory.load_inventory(tmp_path, graph)
    assert "person:lda:lobbyist:154321" not in result["nodes"]
    assert result["counts"]["lda_unique_verified_spine_joins"] == 1
    graph["nodes"]["person:hill:person:two"] = {"lda_source_person_id": "lda:lobbyist:154321"}
    result = inventory.load_inventory(tmp_path, graph)
    assert "person:lda:lobbyist:154321" in result["nodes"]
    assert result["counts"]["lda_ambiguous_spine_joins_kept_separate"] == 1


def test_directory_hash_failure_excludes_entire_source(tmp_path, monkeypatch):
    directory = source_fixture(tmp_path, monkeypatch)
    directory.write_text("{}\n")
    result = inventory.load_inventory(tmp_path, {"nodes": {}})
    assert "politico:unfetched" not in result["nodes"]
    assert any("directory hash mismatch" in error for error in result["exceptions"])


def test_verified_static_spine_reused_without_rehash_or_parse(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fec.surfaces import staffer_graph_snapshot
    monkeypatch.setattr(staffer_graph_snapshot, 'GENERATION', 'staffer_social_network_selected_fixture_primary')
    source_fixture(tmp_path, monkeypatch)
    audits = tmp_path / "audits/staffer_network"
    spine_dir = audits / "spine"
    spine_dir.mkdir()
    receipt_path = spine_dir / "hill_personnel_spine_receipt.json"
    receipt_path.write_text(json.dumps({"conservation": {"observations_admitted": 2, "observation_conservation_holds": True},
        "sources": {"source_rows_suppressed": {"house": 1}, "source_rows_suppressed_by_reason": {"empty": 1}}}))
    graph_dir = audits / staffer_graph_snapshot.GENERATION
    graph_dir.mkdir()
    (graph_dir / "staffer_social_network_receipt.json").write_text(json.dumps({"node_universe": {"person": 1}, "inputs": [
        {"role": "hill_person_spine", "path": str(spine_dir / "deliberately_missing.gz"), "sha256": "verified"},
        {"role": "hill_personnel_spine_receipt", "sha256": inventory.sha(receipt_path)}]}))
    graph = SimpleNamespace(nodes={"person:hill:person:one": {"label": "Existing"}}, verification={"status": "PASS"},
        sources=[{"role": "hill_person_spine", "verified": True, "sha256": "verified"}])
    result = inventory.load_inventory(tmp_path, graph)
    assert result["sourcecoverage"]["payroll"]["reused_verified_static_inventory"]
    assert result["sourcecoverage"]["payroll"]["admitted_source_observations"] == 2
    assert "person:hill:person:one" not in result["nodes"]
    assert not result["exceptions"]


def test_every_lda_person_carries_the_bare_source_id_the_status_join_uses(tmp_path, monkeypatch):
    """The LDA status labels join on `lda_source_person_id` with the `person:`
    prefix stripped (D-022 item 3). This pins the key the inventory hands over."""
    from fec.staffer_network.lda_status_labels import lda_source_ids
    source_fixture(tmp_path, monkeypatch)
    result = inventory.load_inventory(tmp_path, {"nodes": {}})
    lda_people = {ident: node for ident, node in result["nodes"].items()
                  if node.get("verification") == "LDA_SOURCE_PERSON_VERIFIED"}
    assert len(lda_people) == result["counts"]["lda_source_person_ids"] == 2
    joined = {}
    for ident, node in lda_people.items():
        assert node["lda_source_person_id"].startswith("lda:lobbyist:")
        assert not node["lda_source_person_id"].startswith("person:")
        joined[ident] = lda_source_ids(ident, node)
    assert joined == {"person:lda:lobbyist:154321": ["lda:lobbyist:154321"],
                      "person:lda:lobbyist:68016": ["lda:lobbyist:68016"]}
    # Every LDA person is reachable by the join: no unlabelled lobbyist by accident.
    assert all(ids for ids in joined.values())


def test_a_spine_joined_lobbyist_keeps_the_key_on_the_spine_node(tmp_path, monkeypatch):
    from fec.staffer_network.lda_status_labels import lda_source_ids
    source_fixture(tmp_path, monkeypatch)
    spine = "person:hill:person:one"
    graph = {"nodes": {spine: {"id": spine, "kind": "person",
                               "lda_source_person_id": "lda:lobbyist:154321"}}}
    result = inventory.load_inventory(tmp_path, graph)
    # The LDA record folded into the spine person; only the spine id remains.
    assert "person:lda:lobbyist:154321" not in result["nodes"]
    assert result["verified_source_links"]["lda:lobbyist:154321"] == spine
    assert lda_source_ids(spine, graph["nodes"][spine]) == ["lda:lobbyist:154321"]


def test_explicit_user_identity_group_combines_nodes_retains_sources(tmp_path, monkeypatch):
    original_assertions = inventory.USER_ASSERTIONS
    source_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(inventory, "USER_ASSERTIONS", original_assertions)
    graph = {"nodes": {"person:lda:lobbyist:68016": {
        "id": "person:lda:lobbyist:68016", "lda_source_person_id": "lda:lobbyist:68016",
        "label": "KATHLEEN COULOMBE", "employment_history": [{"employer": "Earlier employer"}],
        "provenance": {"source": "original evidence"}}}}
    result = inventory.load_inventory(tmp_path, graph)
    primary = "person:lda:lobbyist:154321"
    alias = "person:lda:lobbyist:68016"
    assert result["identity_aliases"][alias] == primary
    assert alias not in result["nodes"]
    combined = result["nodes"][primary]
    assert combined["source_person_ids"] == ["lda:lobbyist:154321", "lda:lobbyist:68016"]
    assert combined["employment_history"] == [{"employer": "Earlier employer"}]
    assert combined["identity_status"] == "USER_CONFIRMED_SAME_PERSON"
    assert combined["identity_provenance"]["source"] == "user_statement"
    assert len(combined["source_identity_records"]) == 2
    assert result["counts"]["lda_source_person_ids"] == 2
