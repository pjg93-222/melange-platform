"""Verify the descriptive corpus gate and the actual browser profile renderer."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from fec.members.validate_descriptive_profiles import validate_descriptive_profiles


ROOT = Path(__file__).resolve().parents[3]
BUILDER = ROOT / "fec/surfaces/make_member_desk.py"
TEMPLATE = ROOT / "fec/surfaces/templates/make_member_desk_00.tpl"


def _assigned_name(node):
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _gate_namespace(tmp_path, mutate=None):
    names = ["Member %d" % index for index in range(533)]
    row = {"ok": True, "format": "descriptive-v3", "words": 201,
           "recurring_audio_pattern_claimed": False,
           **{key: " ".join([key] * 67) for key in ("p1", "p2", "p3")}}
    document = {
        "schema": "member-narratives-v3-descriptive",
        "status": "ACTIVE_GROUNDED_DESCRIPTIVE",
        "contract_version": "descriptive-public-record-v1",
        "basis_sha256": "basis", "evidence_sha256": "evidence",
        "count": 533, "ok_count": 533,
        "members": {name: copy.deepcopy(row) for name in names},
    }
    receipt = {
        "schema": "member-narratives-v3-descriptive-receipt-v1", "status": "PASS",
        "contract_version": "descriptive-public-record-v1",
        "basis_sha256": "basis", "evidence_sha256": "evidence",
        "counts": {key: 533 for key in ("profiles", "word_count_pass", "grounded_template_pass")},
    }
    if mutate:
        mutate(document, receipt)
    artifact = tmp_path / "profiles.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    receipt["active_artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    namespace = {
        "os": os, "hashlib": hashlib,
        "validate_descriptive_profiles": validate_descriptive_profiles,
        "_NARR_DOC": document, "_NARR_PATH": str(artifact),
        "_NARR_DESCRIPTIVE_RECEIPT": receipt, "_NARR_RECEIPT": {},
        "_BASIS_SHA": "basis", "_SPEECH_PROFILE_SHA": "evidence",
        "MD": {"members": [{"name": name} for name in names]},
    }
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    producer = next(node for node in tree.body if _assigned_name(node) == "_NARR_PRODUCERS")
    start = next(index for index, node in enumerate(tree.body) if _assigned_name(node) == "_NARR_LABELLED")
    end = next(index for index, node in enumerate(tree.body) if _assigned_name(node) == "NARR")
    gate = ast.Module(body=[producer] + tree.body[start:end + 1], type_ignores=[])
    exec(compile(gate, str(BUILDER), "exec"), namespace)
    return namespace


def test_descriptive_profile_gate_accepts_the_complete_hash_bound_corpus(tmp_path):
    namespace = _gate_namespace(tmp_path)
    assert namespace["_NARR_ACTIVE"] is True
    assert len(namespace["NARR"]) == 533


@pytest.mark.parametrize("mutate", [
    lambda doc, receipt: receipt.update(status="PARTIAL"),
    lambda doc, receipt: receipt.update(evidence_sha256="stale"),
    lambda doc, receipt: doc["members"].pop("Member 0"),
    lambda doc, receipt: doc["members"]["Member 0"].update(p1="one word", words=136),
    lambda doc, receipt: doc["members"]["Member 0"].update(p2=""),
    lambda doc, receipt: doc["members"]["Member 0"].update(meeting_playbook={}),
    lambda doc, receipt: doc["members"]["Member 0"].update(recurring_audio_pattern_claimed=True),
])
def test_descriptive_gate_rejects_invalid_content_even_with_a_matching_hash(tmp_path, mutate):
    namespace = _gate_namespace(tmp_path, mutate)
    assert namespace["_NARR_ACTIVE"] is False
    assert namespace["NARR"] == {}


def test_display_payload_excludes_legacy_advice_only_for_descriptive_profiles():
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_narrative_for_display")
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(BUILDER), "exec"), namespace)
    row = {"p1": "one", "p2": "two", "p3": "three", "meeting_playbook": {"open": "legacy"}}
    descriptive = namespace["_narrative_for_display"](row, descriptive=True, has_audio=True)
    legacy = namespace["_narrative_for_display"](row, has_audio=True)
    assert descriptive == {"p1": "one", "p2": "two", "p3": "three",
                           "audio": True, "format": "descriptive-v3"}
    assert legacy["meeting_playbook"] == row["meeting_playbook"]
    assert "format" not in legacy


def _render(row):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the browser renderer")
    template = TEMPLATE.read_text(encoding="utf-8")
    function = template.split("function narrativeHTML(m){", 1)[1].split("function speechHTML(m){", 1)[0]
    script = (
        "const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');\n"
        "function narrativeHTML(m){" + function
        + "process.stdout.write(narrativeHTML(JSON.parse(require('fs').readFileSync(0,'utf8'))));"
    )
    result = subprocess.run([node, "-e", script], input=json.dumps({"psy": {"narrative": row}}),
                            capture_output=True, text=True, check=True, encoding="utf-8")
    return result.stdout


def test_browser_descriptive_profile_suppresses_even_an_injected_legacy_playbook():
    html = _render({
        "format": "descriptive-v3", "p1": "Record <evidence>", "p2": "Communication", "p3": "Context",
        "meeting_playbook": {"open": "STALE_ADVICE", "bring": "STALE_ADVICE", "run_the_room": "STALE_ADVICE",
                             "close": "STALE_ADVICE", "observed_longform": {"summary": "STALE_LONGFORM"}},
    })
    for heading in ("Public record", "Public communication", "Context and evidence"):
        assert heading in html
    assert "Record &lt;evidence&gt;" in html
    for stale in ("STALE_ADVICE", "STALE_LONGFORM", "Suggested approach", "In the room", "Meeting use:"):
        assert stale not in html


def test_browser_keeps_legacy_profile_rendering_unchanged():
    html = _render({"p1": "one", "p2": "two", "p3": "three",
                    "meeting_playbook": {"open": "Legacy open", "bring": "Legacy bring",
                                         "run_the_room": "Legacy room", "close": "Legacy close"}})
    assert "Suggested approach" in html
    assert "In the room" in html
    assert "Legacy open" in html
    assert "Context and evidence" not in html
