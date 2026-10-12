import gzip
import hashlib
import json

from fec.surfaces import staffer_routing_evidence as evidence


def artifact(directory, receipt, name, rows):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    (directory / receipt).write_text(json.dumps({"status": "PASS", "files": [{
        "path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}]}))
    return path


def setup(tmp_path, monkeypatch):
    episode = {"lda_source_person_id": "lda:lobbyist:148612", "registrant_source_org_id": "lda:registrant:11322",
        "display_name": "mr GREGORY MESACK", "employer_name": "AMERICA'S CREDIT UNIONS", "episode_id": "episode:1",
        "start_year": 2024, "end_year": 2026, "filing_years": [2024, 2026], "last_filing_posted_at": "2026-07-17T14:02:51Z"}
    path = artifact(tmp_path / "audits/staffer_network/lda_registrant_employment_20260831",
        "lda_registrant_employment_receipt.json", "lda_registrant_employment_episodes.ndjson.gz", [episode])
    lda = tmp_path / "lda"
    monkeypatch.setattr(evidence, "LDA_ROOT", lda)
    artifact(lda, "lda_source_native_corpus_receipt.json", "lda_organization_assertions.ndjson.gz",
             [{"source_org_id": "lda:registrant:11322", "display_name": "AMERICA'S CREDIT UNIONS"}])
    return path


def test_exact_link_and_filing_date_not_current_employment(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    graph = {"nodes": {"person:verified": {"lda_source_person_id": "lda:lobbyist:148612", "label": "Greg"}}}
    result = evidence.load_evidence(tmp_path, graph)
    assert not result["errors"]
    episode = result["episodes"][0]
    assert episode["person_id"] == "person:verified"
    assert not episode["current_evidence"]
    assert not episode["continuous_employment_asserted"]
    assert episode["filing_years"] == [2024, 2026]
    assert episode["as_of"] == "2026-07-17T14:02:51Z"
    assert result["user_assertions"][0]["evidence_class"] == "USER_ASSERTED"


def test_similar_name_does_not_merge_and_raw_id_is_not_display_name(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    graph = {"nodes": {"person:old": {"lda_source_person_id": "lda:lobbyist:70066", "label": "Greg Mesack"},
                       "organization:unknown": {"label": "organization:unknown"}}}
    result = evidence.load_evidence(tmp_path, graph)
    assert result["episodes"][0]["person_id"] == "person:lda:lobbyist:148612"
    assert "person:lda:lobbyist:148612" in result["nodes"]
    assert result["labels"]["organization:unknown"]["name_status"] == "UNRESOLVED_SOURCE_NAME"


def test_bad_hash_emits_no_employment_evidence(tmp_path, monkeypatch):
    path = setup(tmp_path, monkeypatch)
    with path.open("ab") as stream:
        stream.write(b"bad")
    result = evidence.load_evidence(tmp_path, {"nodes": {}})
    assert result["errors"]
    assert not result["episodes"]


def test_sealed_shared_activity_bounds_and_exact_id_mapping(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    directory = evidence.LDA_ROOT
    receipt = directory / "lda_source_native_corpus_receipt.json"
    source = directory / "lda_industry_edges.ndjson.gz"
    source.write_bytes(b"source")
    metadata = json.loads(receipt.read_text())
    metadata["files"].append({"path": source.name, "sha256": "sourcehash", "bytes": 6})
    receipt.write_text(json.dumps(metadata))
    blob = tmp_path / "named.json"
    blob.write_text(json.dumps({"sources": [{"path": str(source), "sha256": "sourcehash"}],
        "shared_activities": [{"source_activity_id": "lda:activity:filing:0", "filing_uuid": "filing",
            "filing_period": "second_quarter", "filing_year": 2026, "general_issue_code": "BAN",
            "registrant_source_org_id": "lda:registrant:11322", "start": "2026-04-01",
            "end_exclusive": "2026-07-01", "dt_posted": "2026-07-17T14:02:51Z",
            "person_ids": ["lda:lobbyist:148612"]}]}))
    monkeypatch.setattr(evidence, "NAMED_ARTIFACT", blob)
    monkeypatch.setattr(evidence, "NAMED_SHA256", evidence.sha(blob))
    result = evidence.load_evidence(tmp_path, {"nodes": {}})
    assert not result["errors"]
    episode = result["episodes"][-1]
    assert episode["relationship"] == "co_reported_lobbying_activity"
    assert episode["employer_id"] == "activity:lda:activity:filing:0"
    assert episode["person_id"] == "person:lda:lobbyist:148612"
    assert episode["start"] == "2026-04-01" and episode["end_exclusive"] == "2026-07-01"
    assert not episode["current_evidence"]
    with blob.open("a") as stream:
        stream.write(" ")
    rejected = evidence.load_evidence(tmp_path, {"nodes": {}})
    assert rejected["errors"]
    assert not any(e.get("relationship") for e in rejected["episodes"])
