import http.client
from http.server import ThreadingHTTPServer
import gzip
import json
import hashlib
import os
from pathlib import Path
import threading

import pytest

from fec.surfaces.staffer_live_server import Handler, IncrementalProfiles, LiveDashboard


@pytest.mark.parametrize('completion', ['COMPLETE', 'COMPLETE_WITH_EXCEPTIONS'])
def test_completed_collection_retains_age_without_false_staleness(reader, completion):
    checkpoint(reader.raw_dir, 1, completion_status=completion, counts={
        'profile_rows': 1, 'unique_profiles_collected': 1, 'content_bearing_profiles': 0,
        'source_incomplete_profiles': 1, 'accepted_source_exceptions': 1,
        'allowed_profile_unavailable_404': 0}, acquisition_status='ALL_DIRECTORY_RECORDS_ACCOUNTED')
    result = reader.refresh()
    assert result['freshness']['verification_age_seconds'] > 180
    assert result['freshness']['stale'] is False
    assert result['freshness']['collection_status'] == completion
    coverage = result['coverage']
    assert coverage['content_bearing_profiles'] == 0
    assert coverage['source_incomplete_profiles'] == coverage['accepted_source_exceptions'] == 1
    assert coverage['unavailable_profiles'] == 0
    assert coverage['acquisition_status'] == 'ALL_DIRECTORY_RECORDS_ACCOUNTED'


def profile(uuid, **overrides):
    return {
        "profile_uuid": uuid,
        "expected_name": "Staffer " + uuid,
        "verified_name": "Staffer " + uuid,
        "source_url": "https://directory.politicopro.com/congress/staffer/" + uuid,
        "current_header_positions": ["Counsel for Rep. Example"],
        "employment_history": [{"employer": "Example Committee", "title": "Counsel"}],
        "review_status": "REVIEW_REQUIRED",
        **overrides,
    }


def write_rows(path, rows, append=False):
    with path.open("ab" if append else "wb") as stream:
        for row in rows:
            stream.write(json.dumps(row).encode() + b"\n")


def checkpoint(raw, count, collected=None, **overrides):
    receipt = {
        "status": "IN_PROGRESS", "integrity_status": "PASS",
        "completion_status": "INCOMPLETE", "verification_failure_count": 0,
        "generated_at": "2026-09-04T20:00:00Z",
        "counts": {"profile_rows": count, "unique_profiles_collected": count},
        "crawl_state": {"total_succeeded": count},
        "inputs": {"profiles": str(raw / "profiles.ndjson")},
        **overrides,
    }
    (raw / "crawl_verification.json").write_text(json.dumps(receipt), encoding="utf-8")
    (raw / "state.json").write_text(json.dumps({
        "total_succeeded": count if collected is None else collected,
        "queue_total": 18618, "total_failed": 0,
    }), encoding="utf-8")


@pytest.fixture
def reader(tmp_path):
    write_rows(tmp_path / "profiles.ndjson", [profile("one")])
    checkpoint(tmp_path, 1)
    return IncrementalProfiles(tmp_path)


def test_partial_line_is_not_promoted_and_completed_tail_is_read_once(reader):
    reader.refresh()
    row = json.dumps(profile("two")).encode()
    with reader.path.open("ab") as stream:
        stream.write(row[:30])
    checkpoint(reader.raw_dir, 2)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert result["crawl"]["partial_final_line"]
    with reader.path.open("ab") as stream:
        stream.write(row[30:] + b"\n")
    assert reader.refresh()["coverage"]["verified"] == 2
    assert reader.refresh()["coverage"]["collected"] == 2
    assert len(reader.graph()["edges"]) == 4


def test_receipt_covers_only_counted_prefix(reader):
    write_rows(reader.path, [profile("two")], append=True)
    checkpoint(reader.raw_dir, 1, collected=2)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert result["coverage"]["pending"] == 1
    assert reader.graph()["displayed_profiles"] == 1
    checkpoint(reader.raw_dir, 2)
    assert reader.refresh()["coverage"]["verified"] == 2


@pytest.mark.parametrize("override", [
    {"integrity_status": "FAIL"},
    {"verification_failure_count": 1},
    {"crawl_state": {"total_succeeded": 9}},
    {"counts": {"profile_rows": 2, "unique_profiles_collected": 1}},
    {"inputs": {"profiles": "different.ndjson"}},
])
def test_receipt_mismatch_preserves_last_good(reader, override):
    reader.refresh()
    previous = reader.accepted_prefix_sha256
    write_rows(reader.path, [profile("two")], append=True)
    checkpoint(reader.raw_dir, 2, **override)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert result["errors"]
    assert result["crawl"]["last_good_preserved"]
    assert reader.accepted_prefix_sha256 == previous


def test_atomic_repair_requires_new_verification(reader):
    reader.refresh()
    replacement = reader.raw_dir / "replacement.ndjson"
    write_rows(replacement, [profile("replacement")])
    os.replace(replacement, reader.path)
    result = reader.refresh()
    assert result["crawl"]["generation"] == 1
    assert reader.accepted[0]["profile_uuid"] == "one"
    assert result["errors"]
    checkpoint(reader.raw_dir, 1, generated_at="2026-09-04T20:10:00Z")
    assert not reader.refresh()["errors"]
    assert reader.accepted[0]["profile_uuid"] == "replacement"


def test_duplicate_uuid_rejected(reader):
    reader.refresh()
    write_rows(reader.path, [profile("one")], append=True)
    checkpoint(reader.raw_dir, 2)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert "duplicate" in result["errors"][0]


def test_identity_exceptions_are_counted_but_emit_no_graph_evidence(reader):
    write_rows(reader.path, [
        profile("one"),
        profile("missing", identity_status="PROFILE_UNAVAILABLE_404"),
        profile("notes", verified_name="My Notes"),
        profile("changed", identity_status="LIVE_NAME_CHANGED_SAME_UUID"),
    ])
    checkpoint(reader.raw_dir, 4)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 4
    assert result["coverage"]["exceptions"] == 3
    graph = reader.graph()
    assert graph["displayed_profiles"] == 1
    assert all(edge["source"] == "politico:one" for edge in graph["edges"])
    assert all(edge["weight"] == 0 and not edge["operator_included"] for edge in graph["edges"])


def test_source_unavailable_preserves_good_graph(reader):
    reader.refresh()
    before = reader.graph()
    reader.path.unlink()
    result = reader.refresh()
    assert result["errors"]
    assert result["crawl"]["last_good_preserved"]
    assert reader.graph() == before


def test_full_collection_is_still_not_terminal_dashboard_verification(reader):
    checkpoint(reader.raw_dir, 1, status="PASS", completion_status="COMPLETE")
    reader.refresh()
    dashboard = LiveDashboard(reader.raw_dir)
    dashboard.profiles = reader
    result = dashboard.snapshot()
    assert result["status"] == "IN_PROGRESS"
    assert result["terminal_verified"] is False


def test_receipt_changed_during_snapshot_cannot_promote(reader, monkeypatch):
    reader.refresh()
    write_rows(reader.path, [profile("two")], append=True)
    checkpoint(reader.raw_dir, 2)
    original_read = Path.read_bytes
    receipt_path = reader.raw_dir / "crawl_verification.json"
    reads = 0

    def racing_read(path):
        nonlocal reads
        content = original_read(path)
        if path == receipt_path:
            reads += 1
            if reads == 2:
                return content + b" "
        return content

    monkeypatch.setattr(Path, "read_bytes", racing_read)
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert "changed during read" in result["errors"][0]
    monkeypatch.setattr(Path, "read_bytes", original_read)
    assert reader.refresh()["coverage"]["verified"] == 2


def test_replacement_cannot_reuse_preexisting_unaccepted_receipt(reader):
    reader.refresh()
    checkpoint(reader.raw_dir, 2)
    assert reader.refresh()["errors"]
    replacement = reader.raw_dir / "replacement.ndjson"
    write_rows(replacement, [profile("new-one"), profile("new-two")])
    os.replace(replacement, reader.path)
    assert reader.refresh()["coverage"]["verified"] == 1
    assert reader.refresh()["errors"]
    checkpoint(reader.raw_dir, 2, generated_at="2026-09-04T21:00:00Z")
    assert reader.refresh()["coverage"]["verified"] == 2


@pytest.mark.parametrize("nested", [False, True])
def test_hash_bound_replacement_can_be_accepted_immediately(reader, nested):
    reader.refresh()
    replacement = reader.raw_dir / "replacement.ndjson"
    write_rows(replacement, [profile("new")])
    payload = replacement.read_bytes()
    os.replace(replacement, reader.path)
    binding = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    fields = {"profile_prefix": binding} if nested else {
        "profile_prefix_bytes": binding["bytes"], "profile_prefix_sha256": binding["sha256"]}
    checkpoint(reader.raw_dir, 1, **fields)
    assert not reader.refresh()["errors"]
    assert reader.accepted[0]["profile_uuid"] == "new"
    assert "SHA-256" in reader.summary()["crawl"]["verification_basis"]


@pytest.mark.parametrize("wrong_count_boundary", [False, True])
def test_hash_binding_mismatch_keeps_good_snapshot(reader, wrong_count_boundary):
    reader.refresh()
    first_bytes = reader.path.read_bytes()
    write_rows(reader.path, [profile("two")], append=True)
    payload = reader.path.read_bytes()
    checkpoint(reader.raw_dir, 2,
               profile_prefix_bytes=len(first_bytes) if wrong_count_boundary else len(payload),
               profile_prefix_sha256=hashlib.sha256(first_bytes).hexdigest())
    result = reader.refresh()
    assert result["coverage"]["verified"] == 1
    assert result["errors"]


def test_hashed_prefix_can_lag_complete_appended_rows(reader):
    payload = reader.path.read_bytes()
    write_rows(reader.path, [profile("two")], append=True)
    checkpoint(reader.raw_dir, 1, collected=2,
               profile_prefix_bytes=len(payload),
               profile_prefix_sha256=hashlib.sha256(payload).hexdigest())
    result = reader.refresh()
    assert not result["errors"]
    assert result["coverage"]["verified"] == 1
    assert result["coverage"]["pending"] == 1


def replace_with_bound_rows(reader, rows):
    replacement = reader.raw_dir / "replacement.ndjson"
    write_rows(replacement, rows)
    payload = replacement.read_bytes()
    os.replace(replacement, reader.path)
    checkpoint(reader.raw_dir, len(rows), profile_prefix_bytes=len(payload),
               profile_prefix_sha256=hashlib.sha256(payload).hexdigest())


def test_profile_digests_only_publish_after_receipt_and_keep_existing_stable(reader):
    reader.refresh()
    original = dict(reader.profile_digests)
    revision = reader.profile_revision
    write_rows(reader.path, [profile("two")], append=True)
    reader.refresh()
    assert reader.profile_digests == original
    assert reader.profile_revision == revision
    checkpoint(reader.raw_dir, 2)
    reader.refresh()
    assert reader.profile_digests["one"] == original["one"]
    assert reader.profile_changes == {"added": ["two"], "changed": [], "removed": [], "unchanged_count": 1}
    assert reader.profile_revision != revision
    reader.refresh()
    assert reader.profile_changes == {"added": [], "changed": [], "removed": [], "unchanged_count": 2}


def test_changed_profile_content_updates_only_its_digest_after_bound_repair(reader):
    replace_with_bound_rows(reader, [profile("one"), profile("two")])
    reader.refresh()
    old = dict(reader.profile_digests)
    replace_with_bound_rows(reader, [profile("one", verified_name="Verified Full Name"), profile("two")])
    reader.refresh()
    assert reader.profile_changes["changed"] == ["one"]
    assert reader.profile_digests["two"] == old["two"]
    node = next(n for n in reader.graph()["nodes"] if n["id"] == "politico:one")
    assert node["label"] == "Verified Full Name"
    assert node["expected_name"] == "Staffer one"
    assert node["provenance"]["sha256"] == reader.profile_digests["one"]
    assert node["provenance"]["prefix_binding"] == "SHA256_BYTE_PREFIX"
    payload = reader.path.read_bytes()
    proof = node["provenance"]
    assert hashlib.sha256(payload[proof["byte_start"]:proof["byte_end"]]).hexdigest() == proof["raw_line_sha256"]


def test_observation_timestamp_and_exceptions_do_not_change_identity_revision(reader):
    reader.refresh()
    revision = reader.profile_revision
    row_hash = reader.profile_contracts["one"]["profile_row_sha256"]
    replace_with_bound_rows(reader, [profile("one", observed_at="2026-09-04T22:00:00Z"),
                                   profile("missing", identity_status="PROFILE_UNAVAILABLE_404")])
    reader.refresh()
    assert reader.profile_revision == revision
    assert set(reader.eligible_profile_digests) == {"one"}
    assert set(reader.profile_digests) == {"one", "missing"}
    assert reader.profile_contracts["one"]["profile_row_sha256"] != row_hash


def test_failed_receipt_preserves_profile_content_contract(reader):
    reader.refresh()
    contracts = dict(reader.profile_contracts)
    revision = reader.profile_revision
    write_rows(reader.path, [profile("two")], append=True)
    checkpoint(reader.raw_dir, 2, verification_failure_count=1)
    reader.refresh()
    assert reader.profile_contracts == contracts
    assert reader.profile_revision == revision


def test_freshness_age_and_stale_are_evaluated_now_not_at_poll_time(reader):
    from fec.surfaces.staffer_live_server import STALE_AFTER_SECONDS, freshness_now, utcnow
    stored = reader.refresh()["freshness"]
    assert stored["stale"] is True and stored["age_seconds"] > STALE_AFTER_SECONDS  # receipt generated 2026-09-04
    fresh = freshness_now({"verified_at": utcnow(), "age_seconds": 900.0, "stale": True, "poll_seconds": 30})
    assert fresh["stale"] is False and fresh["age_seconds"] < STALE_AFTER_SECONDS and fresh["poll_seconds"] == 30
    stale = freshness_now({"verified_at": "2026-09-04T20:00:00Z", "age_seconds": 1.0, "stale": False})
    assert stale["stale"] is True and stale["age_seconds"] > STALE_AFTER_SECONDS
    assert freshness_now({"stale": False}) == {"stale": False}  # nothing to re-evaluate without verified_at
    assert freshness_now({"verified_at": None})["stale"] is True


class FakeLiveService:
    ready = True
    stage = "Ready"
    metrics = {}
    errors = []

    def __init__(self):
        self.limits = []

    def encoded_snapshot(self, query, limit, since, compressed):
        self.limits.append(limit)
        return json.dumps({"ready": True, "limit": limit}).encode(), '"etag-fixture"'

    def staffer_person_link(self, source_id):
        return {'source_id': source_id, 'person_id': 'person:fixture' if source_id == 'quorum:1' else None,
                'route_available': source_id == 'quorum:1',
                'status': 'matched_current_office' if source_id == 'quorum:1' else 'source_only',
                'match_basis': 'fixture' if source_id == 'quorum:1' else None}


def http_get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_malformed_limit_is_a_client_error_not_a_service_outage():
    service = FakeLiveService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.dashboard = service
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for bad in ("abc", "1e3", "5.5"):
            status, payload = http_get(port, "/api/staffer-live?limit=" + bad)
            assert status == 400, bad
            assert "limit" in payload["error"] and payload.get("status") != "IN_PROGRESS"
        assert service.limits == []
        status, payload = http_get(port, "/api/staffer-live?limit=700")
        assert status == 200 and payload["limit"] == 700
        assert http_get(port, "/api/staffer-live?limit=") == (200, {"ready": True, "limit": 700})  # blank means absent
        assert http_get(port, "/api/staffer-live?limit=-5") == (200, {"ready": True, "limit": 50})  # clamped, as before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_staffer_person_link_route_returns_matched_and_source_only_records():
    server, thread, port = serve(FakeLiveService())
    try:
        assert http_get(port, '/api/staffer-person-link?id=quorum%3A1') == (
            200, {'source_id': 'quorum:1', 'person_id': 'person:fixture',
                  'route_available': True, 'status': 'matched_current_office',
                  'match_basis': 'fixture'})
        status, payload = http_get(port, '/api/staffer-person-link?id=quorum%3A2')
        assert status == 200 and payload['status'] == 'source_only'
        assert payload['person_id'] is None and payload['route_available'] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


# ---------------------------------------------------------------------------
# /api/staffer-groups-index (D-022 item 2)
# ---------------------------------------------------------------------------
def raw_get(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def serve(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.dashboard = service
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


class GroupsService(FakeLiveService):
    """Serves the real builder's output, so the wire shape is the pinned one."""

    def __init__(self):
        super().__init__()
        from types import SimpleNamespace
        from fec.surfaces.staffer_groups_index import build_groups_index
        from fec.surfaces.staffer_network_explorer import build_index
        nodes = {
            "person:hill:person:a": {"id": "person:hill:person:a", "kind": "person", "label": "Ada Staffer",
                "stints": [{"container_id": "container:office:house:a", "title_last": "CHIEF OF STAFF",
                            "status": "current"}]},
            "container:office:house:a": {"id": "container:office:house:a", "kind": "container",
                "node_kind": "container", "container_kind": "member_office", "label": "2025 HON. A"},
            "organization:lda:registrant:1": {"id": "organization:lda:registrant:1", "kind": "organization",
                "node_kind": "organization", "label": "HANCE SCARBOROUGH"},
        }
        facts = SimpleNamespace(
            people={"person:hill:person:a": {"is_current_staff": True, "latest_roles": [
                {"title": "Chief of Staff", "employer_id": "container:office:house:a"}]}},
            features={"person:hill:person:a": {
                "current_employment": {"container:office:house:a": {"label": "2025 HON. A"}},
                "past_employment": {"organization:lda:registrant:1": {"label": "HANCE SCARBOROUGH"}}}},
            coverage={})
        people = build_index(nodes, {}, "rev-1", facts=facts)["payload"]["people"]
        self.built = build_groups_index(people, nodes, "rev-1", facts)
        self.calls = 0

    def groups_index(self, compressed=True):
        self.calls += 1
        return self.built["gzip" if compressed else "raw"], self.built["etag"]


def test_groups_route_serves_offices_and_organisations_with_caching():
    service = GroupsService()
    server, thread, port = serve(service)
    try:
        status, headers, body = raw_get(port, "/api/staffer-groups-index")
        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert headers.get("Content-Encoding") is None
        assert headers["X-Content-Type-Options"] == "nosniff"
        etag = headers["ETag"]
        assert etag.startswith('"groups-')
        payload = json.loads(body)
        assert payload["schema"] == "melange-staffer-groups-index-v1" and payload["ready"] is True
        assert payload["revision"] == "rev-1"
        assert [group["id"] for group in payload["offices"]] == ["container:office:house:a"]
        assert [group["id"] for group in payload["organizations"]] == ["organization:lda:registrant:1"]
        office = payload["offices"][0]
        assert set(office) == {"id", "kind", "name", "people_count", "current_count", "people", "more"}
        assert office["kind"] == "office" and office["current_count"] == 1 and office["more"] == 0
        assert office["people"] == [{"id": "person:hill:person:a", "name": "Ada Staffer",
                                     "current": True, "title": "Chief of Staff"}]
        assert payload["counts"]["offices"] == 1 and payload["counts"]["organizations"] == 1
        # No person row, and nothing institutional, can appear as a top-level record.
        assert "people" not in payload
        status, headers, body = raw_get(port, "/api/staffer-groups-index", {"If-None-Match": etag})
        assert status == 304 and body == b"" and headers["ETag"] == etag
        status, headers, body = raw_get(port, "/api/staffer-groups-index",
                                        {"Accept-Encoding": "gzip"})
        assert status == 200 and headers["Content-Encoding"] == "gzip"
        assert headers["Vary"] == "Accept-Encoding"
        assert json.loads(gzip.decompress(body))["schema"] == "melange-staffer-groups-index-v1"
        assert service.calls == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_groups_route_reports_unavailable_rather_than_crashing_on_an_older_service():
    service = FakeLiveService()  # no groups_index attribute
    server, thread, port = serve(service)
    try:
        status, _, body = raw_get(port, "/api/staffer-groups-index")
        assert status == 503 and json.loads(body)["error"] == "Groups index unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_the_groups_asset_is_served_and_the_people_view_no_longer_offers_record_kinds():
    from fec.surfaces.staffer_live_server import ASSETS
    server, thread, port = serve(FakeLiveService())
    try:
        status, headers, body = raw_get(port, "/staffer-live/staffer-groups.js")
        assert status == 200 and headers["Content-Type"].startswith("application/javascript")
        assert b"staffer-groups-index" in body
        assert raw_get(port, "/staffer-live/not-an-asset.js")[0] == 404
        page = (ASSETS / "index.html").read_text(encoding="utf-8")
        assert 'id="groups-tab"' not in page and '<details id="groups-panel"' in page
        assert 'id="record-kind"' not in page and 'id="network-scope"' not in page
        explorer = (ASSETS / "staffer-explorer.js").read_text(encoding="utf-8")
        assert "$('record-kind')" not in explorer and "$('network-scope')" not in explorer
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
