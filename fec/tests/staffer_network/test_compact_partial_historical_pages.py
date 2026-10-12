import json

from fec.staffer_network.compact_partial_historical_pages import compact


def page(filing_id):
    return {"count": 2, "next": None, "previous": None, "results": [{
        "filing_uuid": filing_id, "filing_year": 2018, "filing_period": "first_quarter",
        "dt_posted": "2018-01-01T00:00:00Z", "registrant": {"id": 1}, "client": {"id": 2},
        "lobbying_activities": [{"general_issue_code": "TAX", "lobbyists": [{
            "lobbyist": {"id": 3, "first_name": "A", "last_name": "Person"},
            "covered_position": "Counsel, Committee, 2010-2012", "new": True}]}]}]}


def test_only_identical_twin_pages_are_authenticated(tmp_path):
    root = tmp_path / "source"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir(parents=True)
    text = json.dumps(page("filing-1"), sort_keys=True)
    (root / "A" / "page_00001.json").write_text(text)
    (root / "B" / "page_00001.json").write_text(text)
    (root / "A" / "page_00002.json").write_text(json.dumps(page("filing-2")))
    receipt = compact(root, tmp_path / "out", 2018)
    assert receipt["authenticated_twin_pages"] == 1
    assert receipt["authenticated_filings"] == 1
    assert receipt["authenticated_lobbyist_observations"] == 1
    assert receipt["single_twin_pages_held"] == 1
    assert receipt["window_complete"] is False
    assert receipt["production_eligible"] is False
