from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_legislation_network_dependents",
    FEC / "legislation" / "run_legislation_network_dependents.py",
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


GENERATION = "a" * 64


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_prerequisites(run_dir: Path) -> Path:
    artifacts = {}
    receipts = {}
    for logical, contract in MOD.REQUIRED_ARTIFACTS.items():
        path = run_dir / contract.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{logical}-fixture\n".encode())
        artifact = {
            "path": contract.path,
            "sha256": digest(path),
            "generation_bound": contract.generation_bound,
        }
        if contract.generation_bound:
            artifact["generation_id"] = GENERATION
            artifact["upstream_receipt"] = contract.upstream_receipt
            receipt_name = contract.upstream_receipt
            assert receipt_name
            receipt_path = (
                run_dir / "pillars_ranks.receipt.json"
                if receipt_name == "pillars_ranks"
                else run_dir / "intermediate" / f"{receipt_name}.receipt.json"
            )
            schema = MOD.EXPECTED_RECEIPT_SCHEMAS.get(
                receipt_name, f"fixture-{receipt_name}-v1"
            )
            payload = {
                "schema": schema,
                "status": "PASS",
                "generation_id": GENERATION,
                "outputs": {
                    logical: {
                        "path": contract.path,
                        "sha256": artifact["sha256"],
                    }
                },
            }
            write_json(receipt_path, payload)
            receipts[receipt_name] = {
                "path": receipt_path.relative_to(run_dir).as_posix(),
                "sha256": digest(receipt_path),
                "generation_id": GENERATION,
            }
        artifacts[logical] = artifact
    document = {
        "schema": MOD.PREREQUISITES_SCHEMA,
        "status": "PASS",
        "generation_id": GENERATION,
        "artifacts": artifacts,
        "upstream_receipts": receipts,
    }
    path = run_dir / MOD.PREREQUISITES_NAME
    write_json(path, document)
    return path


OUTPUT_VALUES = {
    "data/money/electoral_plays.json": {"Member": {"play": "none", "value": 0}},
    "per_topic_alloc.json": {"Topic": {"Member": 1000}},
    "data/lobbying/per_topic_coalition.json": {"Topic": {"Member": 1000}},
    "data/money/budget_frontier.json": {"mode": "coalition", "topics": {"Topic": {}}},
    "data/money/budget_frontier_electoral.json": {
        "mode": "electoral",
        "topics": {"Topic": {}},
    },
    "optimize_result.json": {
        "objective": "expected weighted flips",
        "alloc": {"Member": 1000},
    },
    "im_allocation.json": {
        "flip": {"Member": 1000},
        "coalition": {"Member": 1000},
    },
    "salience_advisory.json": {
        "advisory": [{"issue": "Topic", "value": 1.0}]
    },
}


def make_steps(source_root: Path, *, nondeterministic: str | None = None):
    source_root.mkdir(parents=True, exist_ok=True)
    steps = []
    for index, original in enumerate(MOD.STEPS):
        output = original.outputs[0]
        script = source_root / f"{index:02d}_{original.name}.py"
        value = OUTPUT_VALUES[output]
        body = [
            "import json, os, time",
            "from pathlib import Path",
            f"path=Path(os.environ['MONEYBALL_ROOT'])/{output!r}",
        ]
        if output == nondeterministic:
            body.append(
                f"value={value!r}; value['nonce']=time.time_ns()"
            )
        else:
            body.append(f"value={value!r}")
        body.extend(
            [
                "path.write_text(json.dumps(value,sort_keys=True),encoding='utf-8')",
                "print(path.name)",
            ]
        )
        script.write_text("\n".join(body) + "\n", encoding="utf-8")
        steps.append(
            MOD.Step(
                original.name,
                script,
                original.args,
                original.outputs,
                (),
            )
        )
    return tuple(steps)


def run_fixture(run_dir: Path, steps):
    prerequisites = make_prerequisites(run_dir)
    return MOD.run_recompute(
        run_dir,
        prerequisites_path=prerequisites,
        steps=steps,
        code_capsule=(),
    )


def test_complete_chain_is_generation_bound_and_cross_run_stable(tmp_path):
    source = tmp_path / "shared_source"
    steps = make_steps(source)
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    first = run_fixture(left, steps)
    second = run_fixture(right, steps)

    assert first["status"] == "PASS"
    assert first["generation_id"] == GENERATION
    assert first["idempotence"]["byte_equal"] is True
    assert set(first["declared_outputs"]) == set(OUTPUT_VALUES)
    assert first["artifact_notes"]["per_topic_flip.json"].startswith(
        "not an artifact"
    )
    left_receipt = left / MOD.RECEIPT_NAME
    right_receipt = right / MOD.RECEIPT_NAME
    assert digest(left_receipt) == digest(right_receipt)
    assert first["generation_basis_sha256"] == second["generation_basis_sha256"]
    for name, expected in OUTPUT_VALUES.items():
        assert json.loads((left / name).read_text(encoding="utf-8")) == expected


def test_artifact_hash_mismatch_fails_closed(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prerequisites = make_prerequisites(run_dir)
    (run_dir / "ranks_python.json").write_text("changed", encoding="utf-8")

    with unittest.TestCase().assertRaisesRegex(
        MOD.NetworkDependentsError, "artifact hash mismatch"
    ):
        MOD.validate_prerequisites(run_dir, prerequisites)


def test_mixed_generation_upstream_receipt_fails_closed(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prerequisites = make_prerequisites(run_dir)
    document = json.loads(prerequisites.read_text(encoding="utf-8"))
    document["upstream_receipts"]["pillars_ranks"]["generation_id"] = "b" * 64
    write_json(prerequisites, document)

    with unittest.TestCase().assertRaisesRegex(
        MOD.NetworkDependentsError, "mixed generation"
    ):
        MOD.validate_prerequisites(run_dir, prerequisites)


def test_upstream_receipt_must_hash_bind_its_artifact(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prerequisites = make_prerequisites(run_dir)
    document = json.loads(prerequisites.read_text(encoding="utf-8"))
    receipt_entry = document["upstream_receipts"]["pillars_ranks"]
    receipt_path = run_dir / receipt_entry["path"]
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["outputs"]["ranks"]["sha256"] = "0" * 64
    write_json(receipt_path, payload)
    receipt_entry["sha256"] = digest(receipt_path)
    write_json(prerequisites, document)

    with unittest.TestCase().assertRaisesRegex(
        MOD.NetworkDependentsError, "does not hash-bind"
    ):
        MOD.validate_prerequisites(run_dir, prerequisites)


def test_non_deterministic_legacy_output_fails_closed(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    steps = make_steps(
        tmp_path / "source", nondeterministic="data/money/electoral_plays.json"
    )
    prerequisites = make_prerequisites(run_dir)

    with unittest.TestCase().assertRaisesRegex(
        MOD.NetworkDependentsError, "not byte-idempotent"
    ):
        MOD.run_recompute(
            run_dir,
            prerequisites_path=prerequisites,
            steps=steps,
            code_capsule=(),
        )
    assert not (run_dir / MOD.RECEIPT_NAME).exists()


def test_shadow_environment_and_source_contract_are_fail_closed(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LEGISLATION_SHADOW_MODE", None)
        with unittest.TestCase().assertRaisesRegex(
            MOD.NetworkDependentsError, "SHADOW_MODE"
        ):
            MOD.validate_shadow_environment(run_dir)

    bad_script = tmp_path / "bad.py"
    bad_script.write_text("print('no root contract')\n", encoding="utf-8")
    step = MOD.Step(
        "bad",
        bad_script,
        (),
        ("data/money/electoral_plays.json",),
        ("from paths import ROOT as MB_ROOT",),
    )
    with unittest.TestCase().assertRaisesRegex(
        MOD.NetworkDependentsError, "contract changed"
    ):
        MOD.audit_producers((step,), ())


class LegislationNetworkDependentsTests(unittest.TestCase):
    def with_temp(self, callback) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            callback(Path(temporary))

    def test_complete_chain(self) -> None:
        self.with_temp(test_complete_chain_is_generation_bound_and_cross_run_stable)

    def test_artifact_hash(self) -> None:
        self.with_temp(test_artifact_hash_mismatch_fails_closed)

    def test_mixed_generation(self) -> None:
        self.with_temp(test_mixed_generation_upstream_receipt_fails_closed)

    def test_receipt_hash_binding(self) -> None:
        self.with_temp(test_upstream_receipt_must_hash_bind_its_artifact)

    def test_nondeterminism(self) -> None:
        self.with_temp(test_non_deterministic_legacy_output_fails_closed)

    def test_environment_and_source_contract(self) -> None:
        self.with_temp(
            test_shadow_environment_and_source_contract_are_fail_closed
        )
