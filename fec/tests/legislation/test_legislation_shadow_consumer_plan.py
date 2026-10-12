# -*- coding: utf-8 -*-
"""Focused tests for fail-closed shadow consumer-plan generation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import build_legislation_shadow_consumer_plan as PLAN  # noqa: E402
from legislation import run_legislation_shadow_recompute as SHADOW  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


ROLE_OUTPUTS = {
    name: [
        (path, role)
        for path, role in PLAN.DEFAULT_ISOLATED_OUTPUTS.get(name, [])
        if role is not None
    ]
    for name in SHADOW.REQUIRED_STEP_ORDER
}


def intermediate_outputs(name: str) -> list[str]:
    return [
        path
        for path, role in PLAN.DEFAULT_ISOLATED_OUTPUTS.get(name, [])
        if role is None
    ]


def complete_capabilities(workspace: Path) -> Path:
    producer_dir = workspace / "producers"
    producer_dir.mkdir(parents=True)
    steps = []
    for index, name in enumerate(SHADOW.REQUIRED_STEP_ORDER):
        producer = producer_dir / f"{index:02d}_{name}.py"
        # A sentinel would make accidental execution immediately visible.
        producer.write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_suffix('.RAN').write_text('ran')\n",
            encoding="utf-8",
        )
        outputs = [
            {
                "path": path,
                "baseline_role": role,
                "attribution": {
                    "drivers": ["accepted_legislation"],
                    "rationale": "Fixture declares the accepted-legislation delta.",
                },
            }
            for path, role in ROLE_OUTPUTS[name]
        ]
        outputs.extend(
            {"path": path, "intermediate": True}
            for path in intermediate_outputs(name)
        )
        steps.append(
            {
                "name": name,
                "producer": str(producer.relative_to(workspace)),
                "command": [
                    "{python}",
                    (
                        "{workspace_root}/"
                        + str(producer.relative_to(workspace)).replace("\\", "/")
                    ),
                    "--run-dir",
                    "{run_dir}",
                ],
                "environment": {"MONEYBALL_ROOT": "{run_dir}"},
                "isolated": True,
                "writes_only_under_run_dir": True,
                "outputs": outputs,
            }
        )
    baseline_dir = (
        workspace
        / "audits"
        / "legfix5"
        / "production_baseline_full_downstream"
    )
    baseline_workbook = baseline_dir / "files" / PLAN.BASELINE_WORKBOOK_NAME
    baseline_workbook.parent.mkdir(parents=True)
    baseline_workbook.write_bytes(b"frozen-workbook-fixture\n")
    write_json(
        baseline_dir / "baseline_manifest.json",
        {
            "schema": "legislation-production-baseline-v1",
            "files": [
                {
                    "copy": str(baseline_workbook),
                    "sha256": PLAN.sha256_file(baseline_workbook),
                    "size": baseline_workbook.stat().st_size,
                }
            ],
        },
    )
    verification_dir = (
        workspace / "audits" / "legfix5" / "spreadsheet_engine_verification"
    )
    accepted_verification = verification_dir / (
        "accepted_workbook_verification.json"
    )
    engine_verification = verification_dir / (
        "spreadsheet_engine_verification.json"
    )
    smoke_workbook = verification_dir / "smoke_output.xlsx"
    smoke_inspection = verification_dir / "smoke_inspection.json"
    smoke_workbook.parent.mkdir(parents=True, exist_ok=True)
    smoke_workbook.write_bytes(b"artifact-tool-smoke-fixture\n")
    write_json(smoke_inspection, {"required_sheets_present": True})
    runtime_modules = verification_dir / "node_modules"
    runtime_modules.mkdir()
    accepted_producer = workspace / steps[0]["producer"]
    accepted_helper = workspace / PLAN.DEFAULT_ACCEPTED_WORKBOOK_HELPER
    accepted_helper.parent.mkdir(parents=True, exist_ok=True)
    accepted_helper.write_text(
        '"use strict";\n// artifact-tool fixture helper\n',
        encoding="utf-8",
    )
    write_json(
        accepted_verification,
        {
            "schema": PLAN.ACCEPTED_WORKBOOK_CAPABILITY_SCHEMA,
            "status": "PASS",
            "approved_engine": "@oai/artifact-tool",
            "staged_only": True,
            "production_written": False,
            "producer_sha256": PLAN.sha256_file(accepted_producer),
            "helper_sha256": PLAN.sha256_file(accepted_helper),
            "baseline_input_sha256": PLAN.sha256_file(
                baseline_workbook
            ),
            "smoke_test": {
                "passed": True,
                "required_sheets_present": True,
            },
            "runtime": {
                "artifact_tool_node": sys.executable,
                "artifact_tool_node_modules": str(runtime_modules),
            },
            "output_workbook": {
                "path": str(smoke_workbook.relative_to(workspace)),
                "sha256": PLAN.sha256_file(smoke_workbook),
                "size": smoke_workbook.stat().st_size,
            },
            "inspection": {
                "path": str(smoke_inspection.relative_to(workspace)),
                "sha256": PLAN.sha256_file(smoke_inspection),
                "size": smoke_inspection.stat().st_size,
            },
        },
    )
    write_json(
        engine_verification,
        {
            "schema": (
                "legislation-spreadsheet-engine-import-verification-v1"
            ),
            "status": "PASS",
            "engine": "@oai/artifact-tool",
            "import_completed": True,
            "production_written": False,
            "workbook_edited": False,
            "input": {
                "sha256": PLAN.sha256_file(baseline_workbook),
                "unchanged_after_import": True,
            },
            "missing_required_sheets": [],
        },
    )
    manifest = workspace / "capabilities.json"
    write_json(
        manifest,
        {
            "schema": PLAN.CAPABILITIES_SCHEMA,
            "global_gates": {
                "accepted_ledger_workbook_producer": {
                    "status": "PASS",
                    "producer": steps[0]["producer"],
                    "verification_receipt": str(
                        accepted_verification.relative_to(workspace)
                    ),
                    "verification_sha256": PLAN.sha256_file(
                        accepted_verification
                    ),
                },
                "approved_spreadsheet_engine_import": {
                    "status": "PASS",
                    "producer": steps[10]["producer"],
                    "verification_receipt": str(
                        engine_verification.relative_to(workspace)
                    ),
                    "verification_sha256": PLAN.sha256_file(
                        engine_verification
                    ),
                },
            },
            "steps": steps,
        },
    )
    return manifest


def test_default_repository_capabilities_declare_exact_outputs_without_running():
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "audit"
        result = PLAN.build(
            out_dir=out,
            workspace_root=PLAN.ROOT,
            capabilities_path=None,
        )
        report_path = (
            out / PLAN.PLAN_PATH_NAME
            if result["status"] == "PASS"
            else out / PLAN.AUDIT_PATH_NAME
        )
        assert report_path.is_file()
        audit = json.loads(report_path.read_text(encoding="utf-8"))
        assert audit["production_consumers_executed"] is False
        assert audit["production_written"] is False
        steps = PLAN.default_capabilities(PLAN.ROOT)["steps"]
        rollcall = next(
            row
            for row in steps
            if row["name"] == "materialize_rollcall_aggregate"
        )
        assert len(rollcall["outputs"]) == 9
        base_bipartisan = next(
            row
            for row in steps
            if row["name"]
            == "materialize_base_network_and_prebehavior_bipartisan"
        )
        assert len(base_bipartisan["outputs"]) == 7
        first_say_do = next(
            row
            for row in steps
            if row["name"] == "recompute_say_do"
        )
        second_say_do = next(
            row
            for row in steps
            if row["name"] == "recompute_say_do_second_pass"
        )
        assert len(first_say_do["outputs"]) == 2
        assert len(second_say_do["outputs"]) == 2
        pillars_ranks = next(
            row
            for row in steps
            if row["name"] == "recompute_pillars_and_ranks"
        )
        assert len(pillars_ranks["outputs"]) == 5


def test_unsafe_path_override_blocks_and_removes_runnable_plan():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        capabilities = complete_capabilities(workspace)
        payload = json.loads(capabilities.read_text(encoding="utf-8"))
        payload["steps"][3]["command"] = payload["steps"][3]["command"][:-2]
        payload["steps"][3]["environment"]["MONEYBALL_ROOT"] = str(
            workspace / "production"
        )
        write_json(capabilities, payload)
        out = root / "out"
        out.mkdir()
        (out / PLAN.PLAN_PATH_NAME).write_text(
            '{"stale":true}\n', encoding="utf-8"
        )
        result = PLAN.build(
            out_dir=out,
            workspace_root=workspace,
            capabilities_path=capabilities,
        )
        assert result["status"] == "FAIL"
        assert not (out / PLAN.PLAN_PATH_NAME).exists()
        assert result["stale_runnable_plan_removed"] is True
        issues = [
            row
            for row in result["blocking_issues"]
            if row.get("step") == "recompute_behavior_panel"
        ]
        assert any(row["code"] == "run_dir_confinement_absent" for row in issues)
        assert any(
            row["code"] == "unsafe_environment_path_override"
            for row in issues
        )
        assert not list(workspace.rglob("*.RAN"))


def test_arbitrary_pass_receipt_cannot_satisfy_workbook_capability_gate():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        capabilities = complete_capabilities(workspace)
        payload = json.loads(capabilities.read_text(encoding="utf-8"))
        gate = payload["global_gates"][
            "accepted_ledger_workbook_producer"
        ]
        evidence = workspace / gate["verification_receipt"]
        write_json(evidence, {"status": "PASS"})
        gate["verification_sha256"] = PLAN.sha256_file(evidence)
        write_json(capabilities, payload)
        result = PLAN.build(
            out_dir=root / "out",
            workspace_root=workspace,
            capabilities_path=capabilities,
        )
        assert result["status"] == "FAIL"
        audit = json.loads(
            (root / "out" / PLAN.AUDIT_PATH_NAME).read_text(
                encoding="utf-8"
            )
        )
        accepted = audit["global_gates"][
            "accepted_ledger_workbook_producer"
        ]
        assert accepted["status"] == "FAIL"
        assert accepted["reason"] == "verification receipt contract failed"
        assert accepted["contract_issues"]
        assert not list(workspace.rglob("*.RAN"))


def test_changed_workbook_helper_invalidates_capability_receipt():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        capabilities = complete_capabilities(workspace)
        helper = workspace / PLAN.DEFAULT_ACCEPTED_WORKBOOK_HELPER
        helper.write_text(
            helper.read_text(encoding="utf-8") + "// changed\n",
            encoding="utf-8",
        )
        result = PLAN.build(
            out_dir=root / "out",
            workspace_root=workspace,
            capabilities_path=capabilities,
        )
        assert result["status"] == "FAIL"
        audit = json.loads(
            (root / "out" / PLAN.AUDIT_PATH_NAME).read_text(
                encoding="utf-8"
            )
        )
        accepted = audit["global_gates"][
            "accepted_ledger_workbook_producer"
        ]
        assert any(
            "not bound to current workbook helper source" in issue
            for issue in accepted["contract_issues"]
        )
        assert not list(workspace.rglob("*.RAN"))


def test_globally_unique_role_with_wrong_step_owner_is_blocked():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        capabilities = complete_capabilities(workspace)
        payload = json.loads(capabilities.read_text(encoding="utf-8"))
        pillars = next(
            step
            for step in payload["steps"]
            if step["name"] == "recompute_pillars_and_ranks"
        )
        second_pass = next(
            step
            for step in payload["steps"]
            if step["name"] == "recompute_second_pass_dependents"
        )
        rank_output = next(
            output
            for output in pillars["outputs"]
            if output.get("baseline_role") == "ranks"
        )
        pillars["outputs"].remove(rank_output)
        second_pass["outputs"].append(rank_output)
        write_json(capabilities, payload)
        result = PLAN.build(
            out_dir=root / "out",
            workspace_root=workspace,
            capabilities_path=capabilities,
        )
        assert result["status"] == "FAIL"
        audit = json.loads(
            (root / "out" / PLAN.AUDIT_PATH_NAME).read_text(
                encoding="utf-8"
            )
        )
        codes = {item["code"] for item in audit["blocking_issues"]}
        assert "step_outputs_missing" in codes
        assert "step_outputs_unexpected_or_misowned" in codes
        assert not list(workspace.rglob("*.RAN"))


def test_complete_verified_fixture_emits_exact_executable_plan_without_running():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        capabilities = complete_capabilities(workspace)
        out = root / "out"
        result = PLAN.build(
            out_dir=out,
            workspace_root=workspace,
            capabilities_path=capabilities,
        )
        assert result["status"] == "PASS"
        assert result["plan_emitted"] is True
        audit = json.loads(
            (out / PLAN.AUDIT_PATH_NAME).read_text(encoding="utf-8")
        )
        assert audit["status"] == "PASS"
        assert audit["runnable_plan_present"] is True
        assert audit["blocking_issues"] == []
        plan_path = out / PLAN.PLAN_PATH_NAME
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan["schema"] == SHADOW.PLAN_SCHEMA
        assert tuple(row["name"] for row in plan["steps"]) == (
            SHADOW.REQUIRED_STEP_ORDER
        )
        assert all(
            Path(row["command"][0]).is_file() for row in plan["steps"]
        )
        assert all(
            row["environment"]["MONEYBALL_ROOT"] == "{run_dir}"
            for row in plan["steps"]
        )
        roles = [
            output["baseline_role"]
            for step in plan["steps"]
            for output in step["outputs"]
            if output.get("baseline_role")
        ]
        assert sorted(roles) == sorted(SHADOW.REQUIRED_BASELINE_ROLES)
        assert result["production_consumers_executed"] is False
        assert result["production_written"] is False
        assert not list(workspace.rglob("*.RAN"))


if __name__ == "__main__":
    for name, value in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
