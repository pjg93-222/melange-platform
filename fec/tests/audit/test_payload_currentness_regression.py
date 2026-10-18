"""Temp-only receipt binding controls; never execute an audit producer."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from fec.audit import audit_named_outline_recall_misses as named
from fec.audit import audit_legislation_final_regression_currentness as current


def query_fixture(tmp_path, records=None):
    records = [{"canonical_key": "119:hr:1", "title": "Original"}] if records is None else records
    query = {
        "schema": "congress-advanced-search-query-v1", "query_id": "fixture",
        "primary_instrument": named.PRIMARY,
        "validation": {"exact_result_total": True, "unique_result_total": True, "every_page_exact": True},
        "result_total": len(records), "unique_record_count": len(records),
        "records": records, "records_sha256": hashlib.sha256(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    path = tmp_path / "query.json"
    path.write_text(json.dumps(query), encoding="utf-8")
    item = {**named.file_item(path), "query_id": "fixture", "record_count": len(records),
            "records_sha256": query["records_sha256"]}
    return path, query, item


@pytest.mark.parametrize("change", ["title", "key", "nonobject", "container"])
def test_query_rejects_changed_payload_with_retained_hash(tmp_path, change):
    path, query, item = query_fixture(tmp_path)
    if change == "title":
        query["records"][0]["title"] = "Altered"
    elif change == "key":
        query["records"][0]["canonical_key"] = "119:hr:2"
    elif change == "nonobject":
        query["records"] = ["record"]
        query["records_sha256"] = item["records_sha256"] = named.stable_json_sha256(query["records"])
    else:
        query["records"] = {"one": "record"}
        query["records_sha256"] = item["records_sha256"] = named.stable_json_sha256(query["records"])
    path.write_text(json.dumps(query), encoding="utf-8")
    with pytest.raises(named.ContractError, match="exact closure"):
        named.load_query_artifact(item)


@pytest.mark.parametrize("records", [[], [{"canonical_key": "119:hr:1", "title": "Original"}]])
def test_query_allows_only_outer_metadata_drift(tmp_path, records):
    path, query, item = query_fixture(tmp_path, records)
    assert named.load_query_artifact(item)[3] is True
    query["fetched_at"] = "synthetic later timestamp"
    path.write_text(json.dumps(query, indent=2), encoding="utf-8")
    assert named.load_query_artifact(item)[3] is False


def test_query_accepts_literal_collector_order_and_unicode(tmp_path):
    # Collector JSON.stringify preserves this deliberately nonalphabetic field order.
    payload = '[{"title":"Caf\u00e9 \u2014 energy","canonical_key":"119:hr:1","note":"\u03bb"}]'.encode("utf-8")
    records = json.loads(payload)
    path, query, item = query_fixture(tmp_path, records)
    expected = hashlib.sha256(payload).hexdigest()
    query["records_sha256"] = item["records_sha256"] = expected
    query["fetched_at"] = "metadata may differ"
    path.write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding="utf-8")
    assert named.load_query_artifact(item)[1]["records"] == records
    assert expected != named.stable_json_sha256(records)


def audit_fixture(tmp_path, monkeypatch):
    fec = tmp_path / "fec"
    fec.mkdir()
    worker = fec / "worker.py"
    worker.write_text("VALUE = 1\n", encoding="utf-8")
    inventory = current.current_implementation_test_inventory(tmp_path)
    historical = {"source_test_inventory": copy.deepcopy(inventory)}
    monkeypatch.setattr(current, "validate_historical", lambda *args: (
        historical, {"structural_valid": True, "inventory_binding": current.historical_inventory_binding(historical)}))
    monkeypatch.setattr(current, "validate_rollover", lambda *args: ({}, {"physical_chain_valid": True}))
    monkeypatch.setattr(current, "validate_current_gates", lambda *args: ({}, True))
    monkeypatch.setattr(current, "validate_current_regression", lambda *args: {"valid": False})
    def run():
        missing=tmp_path/"unused.json"
        return current.build_audit(historical_receipt_path=missing, historical_report_path=missing,
            rollover_pointer_path=missing, current_regression_pointer_path=missing, inventory_root=tmp_path)
    return worker, historical, run


@pytest.mark.parametrize("change", ["changed", "added", "removed"])
def test_historical_self_consistency_does_not_certify_current_bytes(tmp_path, monkeypatch, change):
    worker, historical, run = audit_fixture(tmp_path, monkeypatch)
    if change == "changed":
        worker.write_text("VALUE = 2\n", encoding="utf-8")
    elif change == "added":
        worker.with_name("extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    else:
        worker.unlink()
    assert current.historical_inventory_binding(historical)["bound"] is True
    result = run()
    assert result["status"] == "BLOCKED_STALE_HISTORICAL_TEST_INVENTORY"
    assert result["current_implementation_test_inventory"]["historical_tests_certified_current"] is False
    assert result["semantic_chain"]["historical_test_inventory_currentness_valid"] is False
    assert result["authority"]["historical_test_results_currentness_authority"] is False


@pytest.mark.parametrize("with_kind", [True, False])
def test_matching_historical_inventory_can_certify_current_bytes(tmp_path, monkeypatch, with_kind):
    _, historical, run = audit_fixture(tmp_path, monkeypatch)
    inventory=historical["source_test_inventory"]
    if not with_kind:
        for row in inventory["entries"]:
            row.pop("kind", None)
        inventory["inventory_sha256"]=current.stable_sha256(inventory["entries"])
    result=run()
    assert result["status"] == "CURRENT"
    assert result["current_implementation_test_inventory"]["historical_tests_certified_current"] is True


def test_current_regression_can_supersede_stale_history(tmp_path, monkeypatch):
    worker, _, run = audit_fixture(tmp_path, monkeypatch)
    worker.write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(current, "validate_current_regression", lambda *args: {"valid": True})
    result=run()
    assert result["status"] == "CURRENT"
    assert result["current_implementation_test_inventory"]["current_tests_certified_current"] is True
    assert result["current_implementation_test_inventory"]["historical_tests_certified_current"] is False
