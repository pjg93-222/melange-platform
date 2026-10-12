from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec" / "legislation" / "sync_sentinel_bill_ledger.py"

SCHEMA = "legislation-greenlit-integrated-decision-v1"

# Every key the PASS receipt carried before the supplemental source existed. The
# overlay is additive: none of these may disappear to make room for it.
RECEIPT_KEYS_BEFORE = {
    "schema", "status", "completed_at", "engine", "counts", "repository_sha256",
    "verified_history", "discovery", "updates_sha256", "pending_sha256", "plan",
    "output", "runtime_source", "installed", "live_ledger_workbook", "backup",
    "artifact_report", "artifact_report_sha256", "artifact_process_exit",
    "artifact_process_exit_hex", "artifact_process_teardown_nonzero",
}


def load_module():
    spec = importlib.util.spec_from_file_location("sentinel_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _decision(key, state, **extra):
    row = {
        "schema": SCHEMA, "canonical_key": key, "decision_id": "candidate::%s" % key,
        "decision_scope": "candidate", "integration_state": state,
        "ratified_disposition": state, "scoring_eligible": False,
        "ultimate_direction": None, "source_lane": "test_lane",
    }
    row.update(extra)
    return row


def _repository():
    return {"bills": {"119:hr:7": {
        "key": "119:hr:7", "display": "H.R.7", "congress": 119,
        "type": "hr", "number": 7, "title": "Test Act",
        "sponsor": {"name": "Rep. Sponsor"}, "cosponsor_count": 3,
        "introduced_date": "2025-01-03", "update_date": "2026-09-01",
        "latest_action": {"date": "2026-08-31", "text": "Introduced"},
        "policy_area": "Finance", "source": "GovInfo",
        "url": "https://www.congress.gov/bill/119th-congress/house-bill/7",
        "summary": "Summary", "updated_at": "2026-09-02T00:00:00Z",
    }}}


def _discovery():
    return [
        {"canonical_key": "119:hr:10082", "display_number": "H.R.10082",
         "congress": 119, "type": "hr", "number": 10082,
         "candidate_status": "included_unreviewed_candidate"},
        {"canonical_key": "119:s:5335", "display_number": "S.5335",
         "congress": 119, "type": "s", "number": 5335,
         "candidate_status": "included_unreviewed_candidate"},
    ]


def _plan(module, tmp_path, adjudications):
    """build_plan() with a temp repository hash source and a fixed discovery set."""
    repository_path = tmp_path / "repository.json"
    repository_path.write_text("{}", encoding="utf-8")
    saved = module.REPOSITORY
    try:
        module.REPOSITORY = repository_path
        return module.build_plan(
            _repository(), [], [], discovery=_discovery(),
            adjudications=adjudications, discovery_sha256="0" * 64,
            input_path=tmp_path / "in.xlsx", output_path=tmp_path / "out.xlsx",
        )
    finally:
        module.REPOSITORY = saved


def _statuses(plan):
    header = plan["ledger_rows"][0]
    rows = [dict(zip(header, row)) for row in plan["ledger_rows"][1:]]
    return {row["Canonical Bill ID"]: row for row in rows
            if row["Record Type"] == "discovery candidate"}


def test_absent_supplemental_file_leaves_the_plan_byte_identical(tmp_path):
    """NEGATIVE CONTROL: no overlay file == the pre-change code path, to the byte.

    The pre-change main() called build_plan(adjudications=load_ndjson(ADJUDICATIONS)).
    With no supplemental file the new resolution must reduce to exactly that, so the
    two plans are compared as serialized bytes, not as loosely equal objects.
    """
    module = load_module()
    frozen = [_decision("119:hr:10082", "excluded_false_positive")]

    before = _plan(module, tmp_path, frozen)

    rows, block = module.load_supplemental_adjudications(tmp_path / "absent.ndjson")
    merged, overlay = module.merge_adjudications(frozen, rows)
    after = _plan(module, tmp_path, merged)

    assert rows == []
    assert merged == frozen
    assert overlay == {"applied": 0, "collisions": 0, "collision_keys": []}
    assert block["sha256"] is None and block["rows"] == 0
    serialized = lambda plan: json.dumps(plan, indent=2, ensure_ascii=False).encode("utf-8")
    assert serialized(after) == serialized(before)


def test_absent_supplemental_file_is_a_clean_skip_at_the_default_path(tmp_path):
    """Absence is never an error, and the receipt block still describes the miss."""
    module = load_module()
    missing = tmp_path / "data" / "legislation" / "supplemental_adjudications.ndjson"
    saved = module.SUPPLEMENTAL_ADJUDICATIONS
    try:
        module.SUPPLEMENTAL_ADJUDICATIONS = missing
        rows, block = module.load_supplemental_adjudications()
    finally:
        module.SUPPLEMENTAL_ADJUDICATIONS = saved
    assert rows == []
    assert block == {
        "path": str(missing), "sha256": None, "rows": 0,
        "applied": 0, "collisions": 0, "collision_keys": [],
    }


def test_supplemental_row_supplies_review_status_for_a_key_the_frozen_ledger_lacks(tmp_path):
    module = load_module()
    frozen = [_decision("119:hr:10082", "excluded_false_positive")]
    supplemental = [_decision("119:s:5335", "held_visible_non_scoring")]
    merged, overlay = module.merge_adjudications(frozen, supplemental)
    plan = _plan(module, tmp_path, merged)

    rows = _statuses(plan)
    assert rows["119:hr:10082"]["Review Status"] == "excluded_false_positive"
    assert rows["119:s:5335"]["Review Status"] == "held_visible_non_scoring"
    assert overlay == {"applied": 1, "collisions": 0, "collision_keys": []}
    # Both discovery candidates are now adjudicated, so neither is pending review.
    assert plan["counts"]["discovery_pending_review"] == 0
    assert plan["counts"]["discovery_candidates"] == 2

    # Without the overlay the same key falls back to its candidate_status.
    bare = _statuses(_plan(module, tmp_path, frozen))
    assert bare["119:s:5335"]["Review Status"] == "included_unreviewed_candidate"


def test_frozen_ledger_wins_a_colliding_canonical_key_and_the_collision_is_counted(tmp_path):
    """A supplemental row must never overwrite a ratified 2026-07-28 decision."""
    module = load_module()
    frozen = [_decision("119:hr:10082", "excluded_false_positive")]
    supplemental = [
        _decision("119:hr:10082", "included_membership_only_non_scoring"),
        _decision("119:s:5335", "held_visible_non_scoring"),
    ]
    merged, overlay = module.merge_adjudications(frozen, supplemental)
    plan = _plan(module, tmp_path, merged)

    rows = _statuses(plan)
    assert rows["119:hr:10082"]["Review Status"] == "excluded_false_positive"
    assert rows["119:s:5335"]["Review Status"] == "held_visible_non_scoring"
    assert overlay == {
        "applied": 1, "collisions": 1, "collision_keys": ["119:hr:10082"],
    }
    assert merged[0] is frozen[0]
    assert all(row.get("integration_state") != "included_membership_only_non_scoring"
               for row in merged)


def test_collision_keys_are_capped_at_fifty_while_the_count_stays_whole():
    module = load_module()
    keys = ["119:hr:%d" % number for number in range(1000, 1060)]
    frozen = [_decision(key, "excluded_false_positive") for key in keys]
    supplemental = [_decision(key, "held_visible_non_scoring") for key in keys]
    merged, overlay = module.merge_adjudications(frozen, supplemental)
    assert merged == frozen
    assert overlay["applied"] == 0
    assert overlay["collisions"] == 60
    assert len(overlay["collision_keys"]) == 50
    assert overlay["collision_keys"] == sorted(keys)[:50]


@pytest.mark.parametrize("row,fragment", [
    ({"schema": "some-other-schema", "canonical_key": "119:s:1",
      "integration_state": "held_visible_non_scoring"}, "expected"),
    ({"schema": SCHEMA, "canonical_key": "",
      "integration_state": "held_visible_non_scoring"}, "no canonical_key"),
    ({"schema": SCHEMA, "integration_state": "held_visible_non_scoring"}, "no canonical_key"),
    ({"schema": SCHEMA, "canonical_key": "119:s:1"}, "no integration_state"),
    ({"schema": SCHEMA, "canonical_key": "119:s:1", "integration_state": ""},
     "no integration_state"),
])
def test_a_malformed_supplemental_row_raises(tmp_path, row, fragment):
    """A malformed overlay is corrupt evidence, not an environment gap: raise."""
    module = load_module()
    path = tmp_path / "supplemental_adjudications.ndjson"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=fragment):
        module.load_supplemental_adjudications(path)


def test_a_non_object_supplemental_row_raises(tmp_path):
    module = load_module()
    path = tmp_path / "supplemental_adjudications.ndjson"
    path.write_text('["not", "an", "object"]\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="row is not an object"):
        module.load_supplemental_adjudications(path)


def test_unparseable_supplemental_json_raises(tmp_path):
    module = load_module()
    path = tmp_path / "supplemental_adjudications.ndjson"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        module.load_supplemental_adjudications(path)


def test_duplicate_canonical_keys_within_the_supplemental_file_raise(tmp_path):
    module = load_module()
    path = tmp_path / "supplemental_adjudications.ndjson"
    path.write_text("\n".join(json.dumps(row) for row in (
        _decision("119:s:5335", "held_visible_non_scoring"),
        _decision("119:hr:10082", "excluded_false_positive"),
        _decision("119:s:5335", "excluded_false_positive"),
    )) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="repeats canonical_key"):
        module.load_supplemental_adjudications(path)


def test_present_supplemental_file_populates_the_receipt_block(tmp_path):
    module = load_module()
    path = tmp_path / "supplemental_adjudications.ndjson"
    supplemental_rows = (
        _decision("119:hr:10082", "included_membership_only_non_scoring"),
        _decision("119:s:5335", "held_visible_non_scoring"),
    )
    path.write_text("\n".join(json.dumps(row) for row in supplemental_rows) + "\n",
                    encoding="utf-8")
    rows, block = module.load_supplemental_adjudications(path)
    frozen = [_decision("119:hr:10082", "excluded_false_positive")]
    _merged, overlay = module.merge_adjudications(frozen, rows)
    block.update(overlay)

    assert block == {
        "path": str(path), "sha256": module.sha256(path), "rows": 2,
        "applied": 1, "collisions": 1, "collision_keys": ["119:hr:10082"],
    }


def test_supplemental_decision_never_makes_a_discovery_candidate_scoring_eligible(tmp_path):
    """Scoring eligibility is structural; the overlay only supplies Review Status."""
    module = load_module()
    supplemental = [
        _decision("119:hr:10082", "included_membership_only_non_scoring",
                  scoring_eligible=True),
        _decision("119:s:5335", "held_visible_non_scoring", scoring_eligible=True),
    ]
    merged, _overlay = module.merge_adjudications([], supplemental)
    plan = _plan(module, tmp_path, merged)
    rows = _statuses(plan)
    assert len(rows) == 2
    assert all(row["Scoring Eligible"] is False for row in rows.values())
    assert plan["counts"]["verified_interaction_rows"] == 0


def test_receipt_carries_the_supplemental_block_without_dropping_prior_fields():
    """The overlay is additive to the receipt as well as to the decision set."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    literals = [node.value for node in ast.walk(main)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(key, ast.Constant) and key.value == "artifact_report_sha256"
                        for key in node.value.keys)]
    assert len(literals) == 1
    keys = {key.value for key in literals[0].keys if isinstance(key, ast.Constant)}
    assert RECEIPT_KEYS_BEFORE <= keys
    assert "supplemental_adjudications" in keys
    assert keys == RECEIPT_KEYS_BEFORE | {"supplemental_adjudications", "automatic_admissions"}


def test_main_resolves_the_overlay_between_the_frozen_ledger_and_the_plan():
    source = SCRIPT.read_text(encoding="utf-8")
    frozen = source.index("adjudications = load_ndjson(ADJUDICATIONS)")
    load = source.index("supplemental, supplemental_block = load_supplemental_adjudications()")
    merge = source.index("adjudications, overlay = merge_adjudications(adjudications, supplemental)")
    plan = source.index("plan = build_plan(")
    assert frozen < load < merge < plan

    # The frozen ledger is READ and nothing else: its only load-context use in the
    # whole producer is the load_ndjson() call above.
    tree = ast.parse(source)
    reads = [node for node in ast.walk(tree)
             if isinstance(node, ast.Name) and node.id == "ADJUDICATIONS"
             and isinstance(node.ctx, ast.Load)]
    assert len(reads) == 1
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "load_ndjson"
             and any(isinstance(arg, ast.Name) and arg.id == "ADJUDICATIONS"
                     for arg in node.args)]
    assert len(calls) == 1
    assert 'SUPPLEMENTAL_ADJUDICATIONS = ROOT / "data" / "legislation"' in source


def test_shipped_supplemental_overlay_is_valid_and_collision_free():
    """Whatever overlay is installed must satisfy the schema and lose no frozen row."""
    module = load_module()
    if not module.SUPPLEMENTAL_ADJUDICATIONS.is_file():
        pytest.skip("no supplemental overlay installed at %s"
                    % module.SUPPLEMENTAL_ADJUDICATIONS)
    rows, block = module.load_supplemental_adjudications()
    assert block["rows"] == len(rows) > 0
    assert block["sha256"] == module.sha256(module.SUPPLEMENTAL_ADJUDICATIONS)
    frozen = module.load_ndjson(module.ADJUDICATIONS)
    merged, overlay = module.merge_adjudications(frozen, rows)
    assert overlay["collisions"] == 0, overlay["collision_keys"]
    assert overlay["applied"] == len(rows)
    assert len(merged) == len(frozen) + len(rows)
    assert merged[:len(frozen)] == frozen
