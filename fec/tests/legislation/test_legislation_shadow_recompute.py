# -*- coding: utf-8 -*-
"""Focused end-to-end tests for the sealed legislation shadow runner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import run_legislation_shadow_recompute as SHADOW  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def file_item(path: Path, *, relative_to: Path | None = None) -> dict:
    return {
        "path": (
            str(path.relative_to(relative_to))
            if relative_to is not None
            else str(path)
        ),
        "size": path.stat().st_size,
        "sha256": SHADOW.sha256_file(path),
    }


def create_fixture(temp: Path) -> tuple[argparse.Namespace, dict[str, object]]:
    coverage = temp / "coverage"
    registry_hash = SHADOW.sha256_file(SHADOW.REGISTRY)
    query_ids = [f"q{index:02d}" for index in range(62)]
    strict = coverage / "manifest.json"
    write_json(
        strict,
        {
            "schema": "acu-congress-advanced-pages-v1",
            "status": "complete",
            "registry_sha256": registry_hash,
            "selected_query_ids": query_ids,
            "queries": [
                {
                    "query_id": query_id,
                    "validation": {
                        "exact_result_total": True,
                        "unique_result_total": True,
                        "every_page_exact": True,
                    },
                }
                for query_id in query_ids
            ],
        },
    )
    final = coverage / "final_merged" / "manifest.json"
    missed = final.parent / "coverage_gap_ledger.ndjson"
    exclusions = final.parent / "exclusion_ledger.ndjson"
    scope = final.parent / "scope_and_exclusion_contract.json"
    write_text(missed, "")
    write_text(exclusions, "")
    write_json(scope, {"scope": "all"})
    write_json(
        final,
        {
            "schema": "acu-final-merged-v1",
            "status": "complete",
            "acceptance_passed": True,
            "record_count": 2,
            "coverage_gap_count": 0,
            "coverage_evidence": {
                "missed_candidates_ledger": file_item(
                    missed, relative_to=final.parent
                ),
                "exclusion_ledger": file_item(
                    exclusions, relative_to=final.parent
                ),
                "scope_and_exclusion_contract": file_item(
                    scope, relative_to=final.parent
                ),
                "unresolved_gap_count": 0,
                "recovered_candidates_remain_visible": True,
                "semantic_exclusions_are_never_automatic": True,
            },
        },
    )
    review = coverage / "candidate_membership_review" / "manifest.json"
    write_json(
        review,
        {
            "schema": SHADOW.REVIEW_MANIFEST_SCHEMA,
            "discovery_records_are_never_deleted": True,
            "automated_semantic_exclusion": False,
            "official_text_signals_require_review_before_inclusion": True,
            "resolved_decisions_require_reproducible_official_evidence": True,
            "status": "PASS",
            "promotion_gate_passed": True,
            "unresolved_count": 0,
            "weak_provenance_count": 0,
        },
    )
    relationships = coverage / "relationships" / "manifest.json"
    write_json(
        relationships,
        {
            "schema": "legislation-relationships-v1",
            "status": "complete",
            "acceptance_passed": True,
            "all_relationships_reconciled": True,
        },
    )
    reconciliation = (
        coverage / "reconciliation" / "reconciliation_report.json"
    )
    write_json(
        reconciliation,
        {
            "schema": "legislation-coverage-reconciliation-v1",
            "status": "PASS",
            "acceptance": {"passed": True},
        },
    )
    coverage_hashes = {
        "registry_sha256": registry_hash,
        "strict_manifest_sha256": SHADOW.sha256_file(strict),
        "final_manifest_sha256": SHADOW.sha256_file(final),
        "review_manifest_sha256": SHADOW.sha256_file(review),
        "relationships_manifest_sha256": SHADOW.sha256_file(relationships),
        "reconciliation_manifest_sha256": SHADOW.sha256_file(reconciliation),
    }

    accepted_dir = temp / "accepted"
    accepted_relationships = accepted_dir / "accepted_relationships.ndjson"
    write_text(
        accepted_relationships,
        (
            '{"schema":"accepted-legislation-relationship-v1",'
            '"measure_key":"119:hr:1"}\n'
        ),
    )
    manual_bill_evidence = accepted_dir / "manual_bill_evidence.csv"
    write_text(
        manual_bill_evidence,
        "Category,Bill\nPayments,119:hr:1\n",
    )
    accepted_manifest = accepted_dir / "manifest.json"
    write_json(
        accepted_manifest,
        {
            "schema": "accepted-legislation-generation-manifest-v1",
            "status": "complete",
            "generation_complete": True,
            "upstream_acceptance_passed": True,
            "promotion_ready": True,
            "staged_only": True,
            "production_artifacts_written": False,
            "role_weight_policy_reconciled": True,
            "selected_role_weight_policy": "archive_legacy_3_2_1",
            "selected_method_profile": SHADOW.ROLE_METHOD_PROFILE_ID,
            "consumer_role_methods": SHADOW.ROLE_METHOD_PROFILE,
            "input_hashes": coverage_hashes,
            "canonical_ledger_file": accepted_relationships.name,
            "canonical_ledger_sha256": SHADOW.sha256_file(
                accepted_relationships
            ),
            "outputs": {
                "canonical_ledger": file_item(
                    accepted_relationships, relative_to=accepted_dir
                ),
                "manual_bill_evidence": file_item(
                    manual_bill_evidence, relative_to=accepted_dir
                ),
            },
        },
    )

    direction_dir = temp / "direction"
    scoring_directions = direction_dir / "scoring_directions.ndjson"
    write_text(
        scoring_directions,
        (
            '{"canonical_key":"119:hr:1","category":"Payments",'
            '"direction":1}\n'
        ),
    )
    write_json(
        direction_dir / "manifest.json",
        {
            "schema": "legislation-direction-ledger-manifest-v1",
            "status": "PASS",
            "acceptance_passed": True,
            "inputs": {
                "accepted": file_item(accepted_relationships),
            },
            "outputs": {
                "scoring_directions.ndjson": file_item(
                    scoring_directions, relative_to=direction_dir
                )
            },
        },
    )

    base_dir = temp / "base_network"
    network = base_dir / "legislation_base_network.xlsx"
    write_text(network, "fixture network bytes\n")
    write_json(
        base_dir / "manifest.json",
        {
            "schema": "legislation-base-network-manifest-v1",
            "status": "PASS",
            "acceptance_passed": True,
            "inputs": {
                "accepted_ledger": file_item(accepted_relationships),
            },
            "outputs": {
                "workbook": file_item(network, relative_to=base_dir)
            },
        },
    )

    rollcall_dir = temp / "rollcall"
    rollcall_aggregate = rollcall_dir / "official_rollcall_aggregate.json"
    write_json(rollcall_aggregate, {"cells": [], "members": []})
    write_json(
        rollcall_dir / "manifest.json",
        {
            "schema": "official-rollcall-staging-v1",
            "acceptance_passed": True,
            "inputs": [
                file_item(accepted_relationships),
                file_item(scoring_directions),
            ],
            "outputs": {
                "official_rollcall_aggregate.json": file_item(
                    rollcall_aggregate, relative_to=rollcall_dir
                )
            },
        },
    )

    # The ranks baseline is a real member -> rank map so rank drift can be
    # measured rather than inferred from a byte hash.  The recomputed ranking
    # is served from a file the tests can rewrite to induce movement.
    baseline_rank_map = {
        "rank": {f"Member {index:03d}": index + 1 for index in range(12)}
    }
    recomputed_ranks = temp / "recomputed_ranks.json"
    write_json(recomputed_ranks, baseline_rank_map)

    production = temp / "production"
    baseline_files = {}
    baseline_hashes = {}
    for role in SHADOW.REQUIRED_BASELINE_ROLES:
        path = production / f"{role}.json"
        if role == "ranks":
            write_json(path, baseline_rank_map)
        else:
            write_json(path, {"old": True, "role": role})
        baseline_files[role] = file_item(path)
        baseline_hashes[role] = SHADOW.sha256_file(path)
    baseline_manifest = temp / "production_baseline.json"
    write_json(
        baseline_manifest,
        {
            "schema": SHADOW.BASELINE_SCHEMA,
            "scope_complete": True,
            "files": {
                role: {"role": role, **item}
                for role, item in baseline_files.items()
            },
        },
    )

    helper = temp / "isolated_consumer.py"
    helper.write_text(
        "\n".join(
            [
                "import argparse,hashlib,json,zipfile",
                "from pathlib import Path",
                "p=argparse.ArgumentParser()",
                "p.add_argument('--run-dir',required=True)",
                "p.add_argument('--stage',required=True)",
                "p.add_argument('--generation-id',required=True)",
                "p.add_argument('--outputs-json',required=True)",
                "p.add_argument('--ranks-source',required=True)",
                "a=p.parse_args()",
                "root=Path(a.run_dir).resolve()",
                "outputs=json.loads(a.outputs_json)",
                "receipts={'legislation_workbook.receipt.json','intermediate/say_do_first_pass.receipt.json'}",
                "def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()",
                "def item(path):",
                " return {'path':path.relative_to(root).as_posix(),'size':path.stat().st_size,'sha256':digest(path)}",
                "def write_json(path,value):",
                " path.parent.mkdir(parents=True,exist_ok=True)",
                " path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\\n',encoding='utf-8')",
                "def write_xlsx(path):",
                " path.parent.mkdir(parents=True,exist_ok=True)",
                " parts={",
                "  '[Content_Types].xml':'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/><Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/></Types>',",
                "  '_rels/.rels':'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>',",
                "  'xl/workbook.xml':'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"Fixture\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>',",
                "  'xl/_rels/workbook.xml.rels':'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>',",
                "  'xl/worksheets/sheet1.xml':'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>accepted-generation fixture</t></is></c></row></sheetData></worksheet>',",
                " }",
                " with zipfile.ZipFile(path,'w') as archive:",
                "  for name in sorted(parts):",
                "   info=zipfile.ZipInfo(name,(1980,1,1,0,0,0))",
                "   info.compress_type=zipfile.ZIP_DEFLATED",
                "   info.external_attr=0o600 << 16",
                "   archive.writestr(info,parts[name].encode('utf-8'))",
                "for rel in outputs:",
                " if rel in receipts: continue",
                " path=(root/rel).resolve()",
                " if root not in path.parents: raise SystemExit(7)",
                " if path.name=='ranks_python.json':",
                "  path.parent.mkdir(parents=True,exist_ok=True)",
                "  path.write_text(Path(a.ranks_source).read_text(encoding='utf-8'),encoding='utf-8')",
                "  continue",
                " if path.suffix.casefold()=='.xlsx': write_xlsx(path)",
                " else:",
                "  path.parent.mkdir(parents=True,exist_ok=True)",
                "  path.write_text(json.dumps({'path':rel,'stage':a.stage},sort_keys=True)+'\\n',encoding='utf-8')",
                "if 'intermediate/say_do_first_pass.receipt.json' in outputs:",
                " source=root/'say_do_gap.json'",
                " write_json(root/'intermediate/say_do_first_pass.receipt.json',{'schema':'legislation-say-do-pass-receipt-v1','status':'PASS','pass':'first','staged_only':True,'production_written':False,'generation_id':a.generation_id,'root_output':item(source)})",
                "if 'legislation_workbook.receipt.json' in outputs:",
                " workbook=root/'Moneyball Main Version - Blended.xlsx'",
                " snapshot=root/'data' / 'engine' / 'pillar_inputs_snapshot.json'",
                " write_json(root/'legislation_workbook.receipt.json',{'schema':'legislation-workbook-recompute-receipt-v1','status':'PASS','staged_only':True,'production_written':False,'generation_id':a.generation_id,'outputs':{workbook.name:item(workbook),'data/engine/pillar_inputs_snapshot.json':item(snapshot)}})",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    roles_by_step = {
        "materialize_archive_and_legislative_workbook": (
            "archive_leg",
            "legislation_routing_audit",
        ),
        "materialize_rollcall_aggregate": (
            "legacy_rollcall_aggregate",
        ),
        "materialize_base_network_and_prebehavior_bipartisan": (
            "base_network",
            "bipartisan",
        ),
        "recompute_behavior_panel": ("behavior_panel",),
        "recompute_behavioral_dispositions": (
            "behavioral_dispositions",
        ),
        "recompute_cooperation": ("cooperation",),
        "recompute_receptivity": ("receptivity",),
        "recompute_augmented_network": ("augmented_network",),
        "recompute_category_stance": ("category_stance",),
        "recompute_say_do": ("say_do",),
        "recompute_legislative_raw_normalized_sublayer": (
            "legislation_archive_workbook",
            "blended_workbook",
            "pillar_snapshot",
        ),
        "recompute_pillars_and_ranks": ("ranks",),
        "recompute_coalitions_flips_budget_optimizer_allocation": (
            "electoral_plays",
            "per_topic_alloc",
            "per_topic_coalition",
            "budget_frontier",
            "budget_frontier_electoral",
            "optimize_result",
            "im_allocation",
            "salience_advisory",
        ),
        "recompute_dashboard_and_member_desk": (
            "dashboard_bundle",
            "dashboard_html",
            "member_desk",
            "member_desk_html",
        ),
        "recompute_say_do_second_pass": (
            "dashboard_exclusion_reconciliation",
        ),
        "recompute_second_pass_dependents": ("combined_html",),
    }
    assert set(roles_by_step) == set(SHADOW.REQUIRED_STEP_ORDER)
    assert sorted(
        role for group in roles_by_step.values() for role in group
    ) == sorted(SHADOW.REQUIRED_BASELINE_ROLES)
    role_output_paths = {
        "legislation_archive_workbook": (
            "legislation_workbook.receipt.json"
        ),
        "augmented_network": (
            "congress_cosponsor_network_AUG_shadow.xlsx"
        ),
        "bipartisan": "bipartisan.json",
        "receptivity": "member_receptivity.json",
        "category_stance": "category_stance.json",
        "say_do": "say_do_gap.json",
        "blended_workbook": "Moneyball Main Version - Blended.xlsx",
        "pillar_snapshot": "data/engine/pillar_inputs_snapshot.json",
        "ranks": "ranks_python.json",
    }
    generation_outputs = {
        "recompute_say_do": (
            "intermediate/say_do_first_pass.receipt.json",
        ),
    }
    steps = []
    for name in SHADOW.REQUIRED_STEP_ORDER:
        outputs = [
            {
                "path": role_output_paths.get(
                    role, f"artifacts/{role}.json"
                ),
                "baseline_role": role,
                "attribution": {
                    "drivers": ["accepted_legislation"],
                    "rationale": (
                        "Synthetic accepted legislation fixture changes this output."
                    ),
                    "authored_by": "human",
                },
            }
            for role in roles_by_step[name]
        ]
        outputs.extend(
            {
                "path": path,
                "intermediate": True,
            }
            for path in generation_outputs.get(name, ())
        )
        steps.append(
            {
                "name": name,
                "isolated": True,
                "writes_only_under_run_dir": True,
                "command": [
                    sys.executable,
                    str(helper),
                    "--run-dir",
                    "{run_dir}",
                    "--stage",
                    name,
                    "--generation-id",
                    SHADOW.sha256_file(accepted_relationships),
                    "--outputs-json",
                    json.dumps(
                        [output["path"] for output in outputs],
                        separators=(",", ":"),
                    ),
                    "--ranks-source",
                    str(recomputed_ranks),
                ],
                "outputs": outputs,
            }
        )
    plan = temp / "consumer_plan.json"
    write_json(plan, {"schema": SHADOW.PLAN_SCHEMA, "steps": steps})

    args = argparse.Namespace(
        coverage_run=coverage,
        accepted_generation=accepted_dir,
        direction=direction_dir,
        base_network=base_dir,
        rollcall=rollcall_dir,
        baseline_manifest=baseline_manifest,
        consumer_plan=plan,
        out=temp / "shadow",
        strict_discovery=None,
        final_merged=None,
        review=None,
        relationships=None,
        reconciliation=None,
    )
    context = {
        "strict": strict,
        "plan": plan,
        "steps": steps,
        "baseline_hashes": baseline_hashes,
        "baseline_files": {
            role: Path(item["path"]) for role, item in baseline_files.items()
        },
        "accepted_generation_id": SHADOW.sha256_file(
            accepted_relationships
        ),
        "recomputed_ranks": recomputed_ranks,
        "baseline_rank_map": baseline_rank_map,
        "declared_output_count": sum(
            len(step["outputs"]) for step in steps
        ),
    }
    return args, context


def test_full_shadow_pass_is_hash_bound_idempotent_and_production_read_only():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "PASS"
        assert result["acceptance_passed"] is True
        assert result["value_idempotence_passed"] is True
        assert result["byte_idempotence_passed"] is True
        assert result["old_new_delta_audit_passed"] is True
        assert result["production_promotion_performed"] is False
        assert context["declared_output_count"] == 31
        for run_name in ("run_1", "run_2"):
            step_ledger = json.loads(
                (args.out / run_name / SHADOW.STEP_LEDGER_NAME).read_text(
                    encoding="utf-8"
                )
            )
            assert step_ledger["schema"] == SHADOW.STEP_LEDGER_SCHEMA
            assert step_ledger["status"] == "COMPLETE"
            assert (
                step_ledger["generation_id"]
                == context["accepted_generation_id"]
            )
            assert [
                row["name"] for row in step_ledger["completed_steps"]
            ] == list(SHADOW.REQUIRED_STEP_ORDER)
            completed = {
                row["name"]: row
                for row in step_ledger["completed_steps"]
            }
            assert (
                "intermediate/say_do_first_pass.receipt.json"
                in completed["recompute_say_do"]["outputs"]
            )
            workbook_step = completed[
                "recompute_legislative_raw_normalized_sublayer"
            ]
            assert {
                "Moneyball Main Version - Blended.xlsx",
                "data/engine/pillar_inputs_snapshot.json",
                "legislation_workbook.receipt.json",
            }.issubset(workbook_step["outputs"])
            assert (
                "pillars_ranks"
                in completed["recompute_pillars_and_ranks"][
                    "prepared_prerequisites"
                ]
            )
            assert (
                "network_dependents"
                in completed[
                    "recompute_coalitions_flips_budget_optimizer_allocation"
                ]["prepared_prerequisites"]
            )
        receipt_path = args.out / "staged_acceptance_receipt.json"
        assert receipt_path.is_file()
        idempotence = [
            json.loads(line)
            for line in (args.out / "idempotence_audit.ndjson")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(idempotence) == context["declared_output_count"]
        assert all(row["byte_idempotent"] for row in idempotence)
        assert all(row["value_idempotent"] for row in idempotence)
        for role, path in context["baseline_files"].items():
            assert SHADOW.sha256_file(path) == context["baseline_hashes"][role]


def test_strict_discovery_failure_emits_no_acceptance_receipt_or_runs():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        strict = json.loads(context["strict"].read_text(encoding="utf-8"))
        strict["selected_query_ids"] = strict["selected_query_ids"][:-1]
        strict["queries"] = strict["queries"][:-1]
        write_json(context["strict"], strict)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        assert "strict_discovery_gate_failed" in result["blocking_issues"][0]
        assert not (args.out / "staged_acceptance_receipt.json").exists()
        assert not (args.out / "run_1").exists()
        for role, path in context["baseline_files"].items():
            assert SHADOW.sha256_file(path) == context["baseline_hashes"][role]


def test_missing_isolated_consumer_fails_before_execution():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        plan = json.loads(context["plan"].read_text(encoding="utf-8"))
        plan["steps"] = plan["steps"][:-1]
        write_json(context["plan"], plan)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        assert (
            "required_consumer_cannot_be_isolated"
            in result["blocking_issues"][0]
        )
        assert not (args.out / "staged_acceptance_receipt.json").exists()
        assert not (args.out / "run_1").exists()


def test_baseline_hash_mismatch_fails_closed_before_consumers():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        role = SHADOW.REQUIRED_BASELINE_ROLES[0]
        context["baseline_files"][role].write_text(
            "mutated before shadow validation\n", encoding="utf-8"
        )
        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        assert "production_baseline_mutated:initial" in result["blocking_issues"][0]
        assert not (args.out / "staged_acceptance_receipt.json").exists()
        assert not (args.out / "run_1").exists()


def test_unapproved_environment_placeholder_fails_before_consumers():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        plan = json.loads(context["plan"].read_text(encoding="utf-8"))
        plan["steps"][0]["environment"] = {
            "MONEYBALL_ROOT": "{run_dir.__class__}"
        }
        write_json(context["plan"], plan)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        assert "unapproved placeholder" in result["blocking_issues"][0]
        assert not (args.out / "staged_acceptance_receipt.json").exists()
        assert not (args.out / "run_1").exists()


def test_moneyball_root_environment_is_an_explicit_run_binding():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        plan = json.loads(context["plan"].read_text(encoding="utf-8"))
        plan["steps"][0]["command"] = [
            sys.executable,
            str(temp / "environment_bound_consumer.py"),
        ]
        plan["steps"][0]["environment"] = {
            "MONEYBALL_ROOT": "{run_dir}"
        }
        write_json(context["plan"], plan)
        baseline = SHADOW.load_baseline(
            args.baseline_manifest, args.out.resolve()
        )
        _, steps = SHADOW.load_plan(context["plan"], baseline)
        assert steps[0]["environment"] == {
            "MONEYBALL_ROOT": "{run_dir}"
        }


def test_rank_drift_is_measured_and_recorded_not_merely_hash_compared():
    """DEFECT 2: the run must report how far the ranking actually moved."""

    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, _context = create_fixture(temp)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "PASS", result
        assert result["rank_drift_reviewed"] is True
        assert result["rank_drift_within_threshold"] is True
        assert result["rank_drift_members_moved"] == 0
        assert result["rank_drift_max_abs_rank_delta"] == 0
        assert result["rank_drift_roster_stable"] is True
        assert result["rank_drift_threshold"]["max_members_moved"] == 0
        assert (
            result["rank_drift_threshold"]["source"]
            == "default_zero_movement"
        )

        drift_path = args.out / SHADOW.RANK_DRIFT_ARTIFACT_NAME
        assert drift_path.is_file()
        drift = json.loads(drift_path.read_text(encoding="utf-8"))
        assert drift["schema"] == SHADOW.RANK_DRIFT_SCHEMA
        assert drift["members_compared"] == 12
        assert drift["top_movers"] == []
        assert drift["members_added"] == []
        assert drift["members_removed"] == []
        assert result["rank_drift_sha256"] == SHADOW.sha256_file(drift_path)

        audit = json.loads(
            (args.out / "shadow_recompute_audit.json").read_text(
                encoding="utf-8"
            )
        )
        assert audit["gates"]["rank_drift_within_threshold"] is True
        assert audit["rank_drift"]["members_moved"] == 0


def test_rank_movement_blocks_acceptance_and_names_the_top_movers():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        moved = dict(context["baseline_rank_map"]["rank"])
        moved["Member 000"] = 9
        moved["Member 008"] = 1
        moved["Member 003"] = 5
        moved["Member 004"] = 4
        write_json(context["recomputed_ranks"], {"rank": moved})

        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        issue = result["blocking_issues"][0]
        assert "rank_drift_within_threshold" in issue
        assert "members_moved=4" in issue
        assert "max_abs_rank_delta=8" in issue
        assert not (args.out / "staged_acceptance_receipt.json").exists()

        drift = json.loads(
            (args.out / SHADOW.RANK_DRIFT_ARTIFACT_NAME).read_text(
                encoding="utf-8"
            )
        )
        assert drift["members_moved"] == 4
        assert drift["max_abs_rank_delta"] == 8
        assert drift["within_threshold"] is False
        assert [row["member"] for row in drift["top_movers"][:2]] == [
            "Member 000",
            "Member 008",
        ]
        assert drift["top_movers"][0]["baseline_rank"] == 1
        assert drift["top_movers"][0]["recomputed_rank"] == 9


def test_roster_churn_is_drift_even_when_no_shared_member_moves():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        churned = dict(context["baseline_rank_map"]["rank"])
        churned.pop("Member 011")
        churned["Member 099"] = 12
        write_json(context["recomputed_ranks"], {"rank": churned})

        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        drift = json.loads(
            (args.out / SHADOW.RANK_DRIFT_ARTIFACT_NAME).read_text(
                encoding="utf-8"
            )
        )
        assert drift["members_moved"] == 0
        assert drift["members_added"] == ["Member 099"]
        assert drift["members_removed"] == ["Member 011"]
        assert drift["roster_stable"] is False
        assert drift["gate_passed"] is False


def test_explicit_human_allowance_can_admit_bounded_movement():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        moved = dict(context["baseline_rank_map"]["rank"])
        moved["Member 000"] = 2
        moved["Member 001"] = 1
        write_json(context["recomputed_ranks"], {"rank": moved})
        args.max_rank_movers = 2
        args.max_abs_rank_delta = 1

        result = SHADOW.orchestrate(args)
        assert result["status"] == "PASS", result
        assert result["rank_drift_members_moved"] == 2
        assert result["rank_drift_max_abs_rank_delta"] == 1
        assert (
            result["rank_drift_threshold"]["source"]
            == "explicit_human_allowance"
        )


def test_machine_generated_attribution_does_not_explain_a_delta():
    """DEFECT 2/3: auto-filled boilerplate is recorded, never accepted."""

    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, context = create_fixture(temp)
        plan = json.loads(context["plan"].read_text(encoding="utf-8"))
        for step in plan["steps"]:
            for output in step["outputs"]:
                attribution = output.get("attribution")
                if isinstance(attribution, dict):
                    attribution.pop("authored_by", None)
        write_json(context["plan"], plan)

        result = SHADOW.orchestrate(args)
        assert result["status"] == "FAIL"
        issue = result["blocking_issues"][0]
        assert "old_new_delta_attribution" in issue
        assert (
            "machine_generated_attribution_is_not_an_explanation" in issue
        )
        assert not (args.out / "staged_acceptance_receipt.json").exists()


def test_plan_builder_boilerplate_is_machine_authored_even_if_claimed_human():
    boilerplate = {
        "drivers": ["accepted_legislation"],
        "rationale": (
            "This isolated consumer is downstream of the accepted "
            "legislation generation."
        ),
        "authored_by": "human",
    }
    assert SHADOW.attribution_authorship(boilerplate) == "machine_generated"
    assert (
        SHADOW.attribution_authorship(
            {"rationale": "Decay reweighting moved Barr past Hill.",
             "authored_by": "human"}
        )
        == "human"
    )
    assert (
        SHADOW.attribution_authorship(
            {"rationale": "Decay reweighting moved Barr past Hill."}
        )
        == "machine_generated"
    )


def test_gate_provenance_separates_measured_from_precondition_gates():
    """DEFECT 3: hardcoded True must not masquerade as a measurement."""

    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        args, _context = create_fixture(temp)
        result = SHADOW.orchestrate(args)
        assert result["status"] == "PASS", result
        audit = json.loads(
            (args.out / "shadow_recompute_audit.json").read_text(
                encoding="utf-8"
            )
        )
        gates = audit["gates"]
        # The downstream presync audit evaluates all(value is True ...).
        assert all(isinstance(value, bool) for value in gates.values())
        assert all(value is True for value in gates.values())

        provenance = audit["gate_provenance"]
        assert set(provenance) == set(gates)
        measured = {
            name
            for name, row in provenance.items()
            if row["provenance"] == "measured"
        }
        enforced = {
            name
            for name, row in provenance.items()
            if row["provenance"] == "enforced_by_precondition"
        }
        assert measured == {
            "old_new_delta_attribution",
            "rank_drift_within_threshold",
            "cross_run_value_idempotence",
            "cross_run_byte_idempotence",
            "complete_31_role_output_closure",
        }
        assert enforced == set(gates) - measured
        assert audit["measured_gate_count"] == len(measured)
        assert audit["enforced_by_precondition_gate_count"] == len(enforced)
        assert result["measured_gate_count"] == len(measured)
        for name in enforced:
            assert provenance[name]["evidence"] in {
                "validate_coverage",
                "validate_producers",
                "validate_baseline_unchanged",
                "load_plan",
            }
            assert provenance[name]["independently_measured_this_run"] is False
        assert set(SHADOW.GATE_PROVENANCE) == set(gates)


if __name__ == "__main__":
    for name, value in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
